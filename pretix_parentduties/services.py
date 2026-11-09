"""Business logic: product switch, family detection, capacity and lifecycle.

Everything here is strictly scoped to a single event.
"""
import logging

from django.db import transaction
from django.utils.timezone import now
from i18nfield.strings import LazyI18nString
from django.utils.translation import gettext as _

from pretix.base.models import Order
from pretix.base.i18n import language
from pretix.base.reldate import RelativeDateWrapper

from .models import DutySignup, DutySlot, ItemDutyConfig

logger = logging.getLogger(__name__)

SESSION_KEY = "pretix_parentduties"
META_KEY = "pretix_parentduties"

CHOICE_SLOT = "slot"
CHOICE_SIBLING = "sibling"
CHOICE_CANNOT_HELP = "cannot_help"
CHOICE_SKIP = "skip"
CHOICE_FAMILY = "family"

VALID_ORDER_STATES = (Order.STATUS_PENDING, Order.STATUS_PAID)


# ------------------------------------------------------------------ helpers

def normalize_email(email):
    """Strip whitespace and lowercase. Deliberately no provider-specific tricks."""
    if not email:
        return ""
    return "".join(str(email).split()).lower()


def is_live(event):
    return bool(event.settings.parentduties_live)


def triggering_item_ids(event):
    """IDs of all products of the event that trigger the parent duty."""
    default = bool(event.settings.parentduties_new_items_trigger)
    configs = dict(
        ItemDutyConfig.objects.filter(item__event=event).values_list("item_id", "triggers_duty")
    )
    return {
        pk for pk in event.items.values_list("pk", flat=True)
        if configs.get(pk, default)
    }


def item_triggers(item):
    try:
        return item.parentduty_config.triggers_duty
    except ItemDutyConfig.DoesNotExist:
        return bool(item.event.settings.parentduties_new_items_trigger)


def positions_trigger(event, positions):
    """True if at least one of the (cart or order) positions triggers the duty.

    Add-ons and bundled products only count if their own product has the switch;
    variations inherit from their product.
    """
    ids = triggering_item_ids(event)
    return any(p.item_id in ids for p in positions)


def order_triggers(order):
    return positions_trigger(order.event, order.positions.all())


def available_slots(event, item_ids=None, subevent_ids=None):
    """Active slots offered for a cart with the given product IDs, with occupancy."""
    qs = DutySlot.objects.filter(event=event, active=True).with_occupancy().prefetch_related("limit_items")
    result = []
    for slot in qs:
        limits = {i.pk for i in slot.limit_items.all()}
        if limits and item_ids is not None and not (limits & set(item_ids)):
            continue
        if slot.subevent_id and subevent_ids is not None and slot.subevent_id not in subevent_ids:
            continue
        result.append(slot)
    return result


# --------------------------------------------------------- family detection

def find_family_signup(event, email, exclude_order=None):
    """Active signup carrying the duty (``slot`` or ``exempt``) for this e-mail address."""
    email_norm = normalize_email(email)
    if not email_norm:
        return None
    qs = DutySignup.objects.filter(
        event=event,
        email_normalized=email_norm,
        status=DutySignup.STATUS_ACTIVE,
        kind__in=(DutySignup.KIND_SLOT, DutySignup.KIND_EXEMPT),
        order__status__in=VALID_ORDER_STATES,
    )
    if exclude_order is not None:
        qs = qs.exclude(order=exclude_order)
    return qs.order_by("-primary", "id").first()


def find_signup_by_code(event, code, exclude_order=None):
    """Resolve a sibling order code to the root signup carrying the duty, or ``None``."""
    code = (code or "").strip().upper()
    if not code:
        return None
    try:
        order = Order.objects.get(event=event, code=code, status__in=VALID_ORDER_STATES)
    except Order.DoesNotExist:
        return None
    if exclude_order is not None and order.pk == exclude_order.pk:
        return None
    signup = order.parentduty_signups.filter(primary=True).exclude(
        status=DutySignup.STATUS_CANCELED
    ).first()
    if not signup:
        return None
    root = signup.root()
    if root.is_fulfilling and root.order.status in VALID_ORDER_STATES:
        return root
    return None


def family_code(signup):
    if signup is None:
        return ""
    root = signup.root()
    if root.kind == DutySignup.KIND_SIBLING:
        return ""
    return root.order.code


# ------------------------------------------------------------------ capacity

class SlotFull(Exception):
    pass


def check_slot_choice(event, slot_ids):
    """Raise :class:`SlotFull` if one of the chosen slots is no longer available."""
    for slot in DutySlot.objects.filter(event=event, pk__in=slot_ids).with_occupancy():
        if not slot.active or slot.is_full():
            raise SlotFull(slot)
    if DutySlot.objects.filter(event=event, pk__in=slot_ids).count() != len(set(slot_ids)):
        raise SlotFull(None)


def _lock_slot(event, slot_id):
    try:
        return DutySlot.objects.select_for_update().get(event=event, pk=slot_id)
    except (DutySlot.DoesNotExist, ValueError, TypeError):
        return None


# ------------------------------------------------------------ signup creation

def _log(order, action, signup, user=None, **data):
    payload = {
        "signup": signup.pk if signup else None,
        "slot": signup.slot_id if signup else None,
        "slot_name": str(signup.slot) if signup and signup.slot else None,
        "kind": signup.kind if signup else None,
        "status": signup.status if signup else None,
    }
    payload.update(data)
    order.log_action(action, user=user, data=payload)


def create_signups_for_order(order, data=None):
    """Create the signup(s) of a freshly placed order.

    ``data`` is what the checkout step stored in ``order.meta_info``. Orders created
    without the checkout step (backend, API, import) get ``data=None``: they are matched
    by e-mail if possible, otherwise they land in the review list.
    """
    event = order.event
    if not is_live(event):
        return []
    if order.parentduty_signups.exists():
        return []
    if not order_triggers(order):
        return []

    data = data or {}
    choice = data.get("choice")
    if choice == CHOICE_SKIP:
        return []

    email_norm = normalize_email(order.email)
    common = dict(
        event=event,
        order=order,
        email_normalized=email_norm,
        helper_name=(data.get("helper_name") or "")[:255],
        helper_phone=(data.get("helper_phone") or "")[:64],
        note=data.get("note") or "",
    )
    created = []

    with transaction.atomic():
        if choice == CHOICE_SLOT and data.get("slots"):
            for i, slot_id in enumerate(data["slots"][: max_duties(event)]):
                slot = _lock_slot(event, slot_id)
                signup = DutySignup(primary=(i == 0), kind=DutySignup.KIND_SLOT, slot=slot, **common)
                overbooked = slot is None or not slot.active or slot.is_full()
                signup.status = DutySignup.STATUS_NEEDS_REVIEW if overbooked else DutySignup.STATUS_ACTIVE
                signup.save()
                created.append(signup)
                if overbooked:
                    _log(order, "pretix_parentduties.signup.overbooked", signup)
                else:
                    _log(order, "pretix_parentduties.signup.created", signup)
            primary = created[0] if created else None
            if primary is not None and primary.slot_id and waitlist_active(event):
                from .models import DutyWaitlistEntry
                for slot_id in data.get("waitlist") or []:
                    wslot = DutySlot.objects.filter(event=event, pk=slot_id).first()
                    if wslot and wslot.pk not in {c.slot_id for c in created}:
                        DutyWaitlistEntry.objects.get_or_create(
                            slot=wslot, signup=primary, defaults={"event": event},
                        )
                        _log(order, "pretix_parentduties.waitlist.joined", primary,
                             waitlist_slot=wslot.pk, waitlist_slot_name=str(wslot))
        elif choice == CHOICE_SIBLING:
            code = (data.get("sibling_code") or "").strip().upper()
            root = find_signup_by_code(event, code, exclude_order=order)
            signup = DutySignup.objects.create(
                kind=DutySignup.KIND_SIBLING,
                covered_by=root,
                sibling_code=code,
                status=DutySignup.STATUS_ACTIVE if root else DutySignup.STATUS_NEEDS_REVIEW,
                **common,
            )
            created.append(signup)
            _log(order, "pretix_parentduties.signup.created", signup)
        elif choice == CHOICE_CANNOT_HELP:
            signup = DutySignup.objects.create(
                kind=DutySignup.KIND_CANNOT_HELP, status=DutySignup.STATUS_ACTIVE, **common,
            )
            created.append(signup)
            _log(order, "pretix_parentduties.signup.created", signup)
        else:
            # Family already known (checked in the step) or order created outside the checkout.
            root = find_family_signup(event, order.email, exclude_order=order)
            if root:
                signup = DutySignup.objects.create(
                    kind=DutySignup.KIND_SIBLING, covered_by=root, status=DutySignup.STATUS_ACTIVE, **common,
                )
            else:
                # No choice available: open duty, admins assign it.
                signup = DutySignup.objects.create(
                    kind=DutySignup.KIND_SLOT, slot=None, status=DutySignup.STATUS_NEEDS_REVIEW, **common,
                )
            created.append(signup)
            _log(order, "pretix_parentduties.signup.created", signup)
    return created


def max_duties(event):
    if event.settings.parentduties_allow_multiple:
        return max(1, int(event.settings.parentduties_max_per_family or 1))
    return 1


# ----------------------------------------------------------------- lifecycle

def _flag_dependents(signup, reason, user=None):
    """Siblings relying on ``signup`` go to review."""
    for dep in DutySignup.objects.filter(covered_by=signup).exclude(
        status__in=(DutySignup.STATUS_CANCELED, DutySignup.STATUS_NEEDS_REVIEW)
    ):
        dep.status = DutySignup.STATUS_NEEDS_REVIEW
        dep.save(update_fields=["status"])
        _log(dep.order, "pretix_parentduties.signup.needs_review", dep, user=user, reason=reason)


def cancel_order_signups(order, user=None):
    freed = []
    with transaction.atomic():
        for signup in order.parentduty_signups.exclude(status=DutySignup.STATUS_CANCELED):
            if signup.kind == DutySignup.KIND_SLOT and signup.status == DutySignup.STATUS_ACTIVE:
                freed.append(signup.slot)
            signup.status = DutySignup.STATUS_CANCELED
            signup.save(update_fields=["status"])
            close_waitlist_entries(signup)
            _log(order, "pretix_parentduties.signup.canceled", signup, user=user)
            _flag_dependents(signup, "family_has_no_duty", user=user)
    process_waitlist_for(*freed)


def reactivate_order_signups(order, user=None):
    event = order.event
    with transaction.atomic():
        for signup in order.parentduty_signups.filter(status=DutySignup.STATUS_CANCELED):
            if signup.kind == DutySignup.KIND_SLOT:
                slot = _lock_slot(event, signup.slot_id) if signup.slot_id else None
                ok = slot is not None and slot.active and not slot.is_full()
            elif signup.kind == DutySignup.KIND_SIBLING:
                root = signup.root() if signup.covered_by_id else None
                ok = bool(root and root.is_fulfilling and root.pk != signup.pk)
            else:
                ok = True
            signup.status = DutySignup.STATUS_ACTIVE if ok else DutySignup.STATUS_NEEDS_REVIEW
            signup.save(update_fields=["status"])
            _log(order, "pretix_parentduties.signup.reactivated", signup, user=user)


def handle_order_split(original, split_order):
    """The duty stays with the original order; the new order is covered by it."""
    primary = original.parentduty_signups.filter(primary=True).exclude(
        status=DutySignup.STATUS_CANCELED
    ).first()
    if not primary or split_order.parentduty_signups.exists():
        return None
    root = primary.root()
    signup = DutySignup.objects.create(
        event=original.event,
        order=split_order,
        kind=DutySignup.KIND_SIBLING,
        covered_by=root,
        email_normalized=normalize_email(split_order.email),
        status=DutySignup.STATUS_ACTIVE if root.is_fulfilling else DutySignup.STATUS_NEEDS_REVIEW,
    )
    _log(split_order, "pretix_parentduties.signup.created", signup)
    return signup


def handle_order_changed(order):
    """No triggering product left: send active signups to review, never delete."""
    if order_triggers(order):
        return
    freed = []
    for signup in order.parentduty_signups.filter(status=DutySignup.STATUS_ACTIVE):
        if signup.kind == DutySignup.KIND_SLOT:
            freed.append(signup.slot)
        signup.status = DutySignup.STATUS_NEEDS_REVIEW
        signup.save(update_fields=["status"])
        _log(order, "pretix_parentduties.signup.needs_review", signup, reason="no_triggering_product")
    process_waitlist_for(*freed)


def delete_order_signups(order):
    """Test mode order is deleted for good."""
    signups = list(order.parentduty_signups.select_related("slot"))
    for signup in signups:
        _flag_dependents(signup, "family_has_no_duty")
    freed = [s.slot for s in signups if s.kind == DutySignup.KIND_SLOT and s.status == DutySignup.STATUS_ACTIVE]
    DutySignup.objects.filter(pk__in=[s.pk for s in signups]).delete()
    process_waitlist_for(*freed)


# --------------------------------------------------------- self-service change

def change_deadline(event):
    """Datetime until which parents may change their duty, or ``None`` (admins only)."""
    wrapper = event.settings.get("parentduties_change_until", as_type=RelativeDateWrapper)
    if not wrapper:
        return None
    return wrapper.datetime(event)


def can_change(event):
    deadline = change_deadline(event)
    return bool(deadline and now() <= deadline and is_live(event))


def changeable_signups(order):
    """Signups the parents may change themselves.

    Only own duties (assigned or still open) and "cannot help" can be changed into a
    slot. Siblings change at the root order, exemptions are admin decisions. There is
    no way to withdraw: the result is always a slot.
    """
    if order.status not in VALID_ORDER_STATES or not can_change(order.event):
        return []
    return [
        s for s in order.parentduty_signups.select_related("slot").order_by("-primary", "id")
        if s.status != DutySignup.STATUS_CANCELED
        and s.kind in (DutySignup.KIND_SLOT, DutySignup.KIND_CANNOT_HELP)
    ]


def customer_change(signup, slot, helper_name=None, helper_phone=None):
    """Move a signup to ``slot`` on behalf of the parents. Raises :class:`SlotFull`."""
    event = signup.event
    with transaction.atomic():
        locked = _lock_slot(event, slot.pk)
        if locked is None or not locked.active:
            raise SlotFull(slot)
        if locked.pk != signup.slot_id and locked.is_full():
            raise SlotFull(locked)
        if locked.pk != signup.slot_id and signup.order.parentduty_signups.filter(
            slot=locked, kind=DutySignup.KIND_SLOT,
        ).exclude(pk=signup.pk).exclude(status=DutySignup.STATUS_CANCELED).exists():
            raise SlotFull(locked)
        old = signup.slot
        was_active = signup.kind == DutySignup.KIND_SLOT and signup.status == DutySignup.STATUS_ACTIVE
        if signup.kind == DutySignup.KIND_CANNOT_HELP:
            signup.note = ""  # the "cannot help" reason no longer applies
        signup.kind = DutySignup.KIND_SLOT
        signup.slot = locked
        signup.covered_by = None
        # A free slot was chosen, so overbooking / open duty / "cannot help" are resolved.
        signup.status = DutySignup.STATUS_ACTIVE
        if helper_name is not None:
            signup.helper_name = helper_name[:255]
        if helper_phone is not None:
            signup.helper_phone = helper_phone[:64]
        signup.save()
        close_waitlist_entries(signup, fulfilled_slot=locked)
        _log(signup.order, "pretix_parentduties.signup.changed_by_customer", signup,
             old_slot=old.pk if old else None, old_slot_name=str(old) if old else None)
    if old is not None and old.pk != locked.pk and was_active:
        process_waitlist_for(old)
    return signup


# ------------------------------------------------------- admin interventions

def admin_move(signup, slot, user=None):
    """Move a signup to another slot. Admins may deliberately exceed capacity."""
    old = signup.slot
    was_active = signup.kind == DutySignup.KIND_SLOT and signup.status == DutySignup.STATUS_ACTIVE
    signup.kind = DutySignup.KIND_SLOT
    signup.slot = slot
    signup.covered_by = None
    signup.status = DutySignup.STATUS_ACTIVE
    signup.save()
    close_waitlist_entries(signup, fulfilled_slot=slot)
    _log(signup.order, "pretix_parentduties.signup.moved", signup, user=user,
         old_slot=old.pk if old else None, old_slot_name=str(old) if old else None)
    if old is not None and old.pk != slot.pk and was_active:
        process_waitlist_for(old)


def admin_set(signup, kind, status, user=None, covered_by=None):
    old_slot = signup.slot if (
        signup.kind == DutySignup.KIND_SLOT and signup.status == DutySignup.STATUS_ACTIVE
    ) else None
    signup.kind = kind
    signup.status = status
    if kind != DutySignup.KIND_SLOT:
        signup.slot = None
    if kind == DutySignup.KIND_SIBLING:
        signup.covered_by = covered_by
    else:
        signup.covered_by = None
    signup.save()
    _log(signup.order, "pretix_parentduties.signup.changed", signup, user=user)
    if kind != DutySignup.KIND_SLOT:
        close_waitlist_entries(signup)
    if not signup.is_fulfilling:
        _flag_dependents(signup, "family_has_no_duty", user=user)
    still_there = signup.kind == DutySignup.KIND_SLOT and signup.status == DutySignup.STATUS_ACTIVE \
        and signup.slot_id == (old_slot.pk if old_slot else None)
    if old_slot is not None and not still_there:
        process_waitlist_for(old_slot)


def admin_add(order, slot=None, kind=DutySignup.KIND_SLOT, user=None):
    signup = DutySignup.objects.create(
        event=order.event,
        order=order,
        primary=not order.parentduty_signups.filter(primary=True).exists(),
        kind=kind,
        slot=slot if kind == DutySignup.KIND_SLOT else None,
        email_normalized=normalize_email(order.email),
        status=DutySignup.STATUS_ACTIVE,
    )
    _log(order, "pretix_parentduties.signup.created", signup, user=user)
    return signup


def admin_cancel(signup, user=None):
    freed = signup.slot if (
        signup.kind == DutySignup.KIND_SLOT and signup.status == DutySignup.STATUS_ACTIVE
    ) else None
    signup.status = DutySignup.STATUS_CANCELED
    signup.save(update_fields=["status"])
    close_waitlist_entries(signup)
    _log(signup.order, "pretix_parentduties.signup.canceled", signup, user=user)
    _flag_dependents(signup, "family_has_no_duty", user=user)
    if signup.primary:
        # promote another active signup of the order, if any
        nxt = signup.order.parentduty_signups.exclude(pk=signup.pk).exclude(
            status=DutySignup.STATUS_CANCELED
        ).order_by("id").first()
        if nxt:
            signup.primary = False
            signup.save(update_fields=["primary"])
            nxt.primary = True
            nxt.save(update_fields=["primary"])
    process_waitlist_for(freed)


# ------------------------------------------------------------------ copying

def copy_slots(source_event, target_event, item_map):
    for slot in DutySlot.objects.filter(event=source_event).prefetch_related("limit_items"):
        items = list(slot.limit_items.all())
        slot.pk = None
        slot.id = None
        slot.event = target_event
        slot.subevent = None
        slot.date = None
        slot.save()
        new_items = [item_map[i.pk] for i in items if i.pk in item_map]
        slot.limit_items.set(new_items)
    for cfg in ItemDutyConfig.objects.filter(item__event=source_event):
        new_item = item_map.get(cfg.item_id)
        if new_item:
            ItemDutyConfig.objects.update_or_create(
                item=new_item, defaults={"triggers_duty": cfg.triggers_duty},
            )


def status_label(signup):
    if signup is None:
        return ""
    if signup.status == DutySignup.STATUS_NEEDS_REVIEW:
        return _("under review")
    if signup.status == DutySignup.STATUS_CANCELED:
        return _("canceled")
    return {
        DutySignup.KIND_SLOT: _("committed"),
        DutySignup.KIND_SIBLING: _("covered by sibling"),
        DutySignup.KIND_EXEMPT: _("exempt"),
        DutySignup.KIND_CANNOT_HELP: _("cannot help"),
    }.get(signup.kind, "")


# ------------------------------------------------------------------ waitlist

def waitlist_mode(event):
    from .conf import WAITLIST_OFF
    return event.settings.parentduties_waitlist_mode or WAITLIST_OFF


def waitlist_active(event):
    """Waitlist offered and processed: mode on, plugin live, before the event and the deadline."""
    from .conf import WAITLIST_NOTIFY, WAITLIST_OFF
    mode = waitlist_mode(event)
    if mode == WAITLIST_OFF or not is_live(event):
        return False
    if event.date_from and now() >= event.date_from:
        return False
    deadline = change_deadline(event)
    if mode == WAITLIST_NOTIFY:
        # parents must be able to switch themselves
        return can_change(event)
    return deadline is None or now() <= deadline


def can_join_waitlist(signup, slot):
    return (
        waitlist_active(signup.event)
        and signup.kind == DutySignup.KIND_SLOT
        and signup.status != DutySignup.STATUS_CANCELED
        and signup.order.status in VALID_ORDER_STATES
        and slot.active
        and slot.pk != signup.slot_id
        and slot.is_full()
    )


def join_waitlist(signup, slot):
    from .models import DutyWaitlistEntry
    entry, created = DutyWaitlistEntry.objects.get_or_create(
        slot=slot, signup=signup, defaults={"event": signup.event},
    )
    if not created and entry.status != DutyWaitlistEntry.STATUS_WAITING:
        entry.status = DutyWaitlistEntry.STATUS_WAITING
        entry.notified_at = None
        entry.created = now()
        entry.save(update_fields=["status", "notified_at", "created"])
    _log(signup.order, "pretix_parentduties.waitlist.joined", signup, waitlist_slot=slot.pk,
         waitlist_slot_name=str(slot))
    return entry


def leave_waitlist(entry, user=None):
    from .models import DutyWaitlistEntry
    entry.status = DutyWaitlistEntry.STATUS_REMOVED
    entry.save(update_fields=["status"])
    _log(entry.signup.order, "pretix_parentduties.waitlist.left", entry.signup, user=user,
         waitlist_slot=entry.slot_id, waitlist_slot_name=str(entry.slot))


def close_waitlist_entries(signup, fulfilled_slot=None):
    """After a move or cancellation: the reached slot is fulfilled, all others are removed."""
    from .models import DutyWaitlistEntry
    qs = DutyWaitlistEntry.objects.filter(signup=signup, status=DutyWaitlistEntry.STATUS_WAITING)
    if fulfilled_slot is not None:
        qs.filter(slot=fulfilled_slot).update(status=DutyWaitlistEntry.STATUS_FULFILLED)
        qs = qs.exclude(slot=fulfilled_slot)
    qs.update(status=DutyWaitlistEntry.STATUS_REMOVED)


def _waitlist_mail(signup, entry, kind):
    from pretix.base.email import get_email_context
    event = signup.event
    order = signup.order
    subject = event.settings.get(f"parentduties_waitlist_{kind}_subject", as_type=LazyI18nString)
    text = event.settings.get(f"parentduties_waitlist_{kind}_text", as_type=LazyI18nString)
    with language(order.locale, event.settings.region):
        ctx = get_email_context(event=event, order=order)
        ctx["parent_duty_waitlist_slot"] = str(entry.slot.name)
        try:
            order.send_mail(subject, text, ctx, log_entry_type=f"pretix_parentduties.waitlist.mail_{kind}")
        except Exception:  # never let a mail problem break the move
            logger.exception("Could not send parent duty waitlist e-mail")


def process_waitlist(slot):
    """Hand out free spots of ``slot`` (and of slots freed in turn) to the waitlist, oldest first."""
    from .conf import WAITLIST_AUTO
    from .models import DutyWaitlistEntry
    event = slot.event
    if not waitlist_active(event):
        return
    auto = waitlist_mode(event) == WAITLIST_AUTO
    queue = [slot.pk]
    rounds = 0
    while queue and rounds < 1000:
        rounds += 1
        slot_id = queue.pop(0)
        with transaction.atomic():
            current = _lock_slot(event, slot_id)
            if current is None or not current.active:
                continue
            entries = DutyWaitlistEntry.objects.filter(
                slot=current, status=DutyWaitlistEntry.STATUS_WAITING,
            ).select_related("signup__order", "signup__slot").order_by("created", "id")
            if not auto:
                entries = entries.filter(notified_at__isnull=True)
            free = current.free_count()
            for entry in entries:
                if free is not None and free <= 0:
                    break
                signup = entry.signup
                valid = (
                    signup.kind == DutySignup.KIND_SLOT
                    and signup.status != DutySignup.STATUS_CANCELED
                    and signup.order.status in VALID_ORDER_STATES
                )
                if not valid:
                    entry.status = DutyWaitlistEntry.STATUS_REMOVED
                    entry.save(update_fields=["status"])
                    continue
                if signup.slot_id == current.pk:
                    entry.status = DutyWaitlistEntry.STATUS_FULFILLED
                    entry.save(update_fields=["status"])
                    continue
                if auto:
                    old = signup.slot
                    was_active = signup.status == DutySignup.STATUS_ACTIVE
                    signup.slot = current
                    signup.status = DutySignup.STATUS_ACTIVE
                    signup.save(update_fields=["slot", "status"])
                    close_waitlist_entries(signup, fulfilled_slot=current)
                    _log(signup.order, "pretix_parentduties.waitlist.moved", signup,
                         old_slot=old.pk if old else None, old_slot_name=str(old) if old else None)
                    _waitlist_mail(signup, entry, "auto")
                    if old is not None and was_active:
                        queue.append(old.pk)
                else:
                    entry.notified_at = now()
                    entry.save(update_fields=["notified_at"])
                    _log(signup.order, "pretix_parentduties.waitlist.notified", signup,
                         waitlist_slot=current.pk, waitlist_slot_name=str(current))
                    _waitlist_mail(signup, entry, "notify")
                if free is not None:
                    free -= 1


def process_waitlist_for(*slots):
    seen = set()
    for slot in slots:
        if slot is not None and slot.pk not in seen:
            seen.add(slot.pk)
            process_waitlist(slot)
