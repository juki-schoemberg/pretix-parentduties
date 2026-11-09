import json
from typing import List, Tuple

from django.dispatch import receiver
from django.template.loader import get_template
from django.urls import resolve, reverse
from django.utils.translation import gettext_lazy as _, pgettext_lazy

from pretix.api.signals import order_api_details
from pretix.base.notifications import ParametrizedOrderNotificationType
from pretix.base.permissions import OPTS_READ_WRITE, PermissionGroup
from pretix.base.services.orders import OrderError
from pretix.base.shredder import BaseDataShredder
from pretix.base.signals import (
    event_copy_data, item_copy_data, order_canceled, order_changed, order_expired,
    order_gracefully_delete, order_placed, order_reactivated, order_split,
    register_data_shredders, register_event_permission_groups,
    register_notification_types, validate_order,
)
from pretix.control.signals import (
    event_dashboard_widgets, item_forms, nav_event, nav_event_settings,
    order_info as control_order_info,
)
from pretix.helpers.json import CustomJSONEncoder
from pretix.presale.signals import (
    checkout_confirm_page_content, checkout_flow_steps, order_info as presale_order_info,
    order_meta_from_request,
)

from . import conf  # noqa: F401  (registers settings defaults)
from .models import DutySignup, ItemDutyConfig
from .services import (
    CHOICE_SLOT, META_KEY, SESSION_KEY, SlotFull, cancel_order_signups, check_slot_choice,
    copy_slots, create_signups_for_order, delete_order_signups, handle_order_changed,
    handle_order_split, is_live, positions_trigger, reactivate_order_signups,
)

DUID = "pretix_parentduties"
PERMISSION_GROUP = "event.parentduties"
PERM_READ = f"{PERMISSION_GROUP}:read"
PERM_WRITE = f"{PERMISSION_GROUP}:write"


def has_perm(request, perm):
    return request.user.has_event_permission(request.organizer, request.event, perm, request=request)


# ------------------------------------------------------------ permissions

@receiver(register_event_permission_groups, dispatch_uid=f"{DUID}_permissions")
def register_permissions(sender, **kwargs):
    # Must be a list: PermissionGroup is a NamedTuple and would be iterated otherwise.
    return [PermissionGroup(
        name=PERMISSION_GROUP,
        label=_("Parent duties"),
        actions=["read", "write"],
        options=OPTS_READ_WRITE,
        help_text=_("Manage duty slots and the duties families have signed up for."),
    )]


# --------------------------------------------------------------- checkout

@receiver(checkout_flow_steps, dispatch_uid=f"{DUID}_checkout_step")
def register_checkout_step(sender, **kwargs):
    from .checkoutflow import ParentDutyStep
    return ParentDutyStep


def _session_data(request):
    from pretix.presale.views.cart import cart_session
    cs = cart_session(request, create=False)
    if not cs:
        return None
    return cs.get(SESSION_KEY)


@receiver(order_meta_from_request, dispatch_uid=f"{DUID}_order_meta")
def order_meta(sender, request, **kwargs):
    if not is_live(sender):
        return {}
    data = dict(_session_data(request) or {})
    if sender.settings.parentduties_confirm_checkbox and data.get("choice") == CHOICE_SLOT:
        data["committed"] = request.POST.get("parentduties_commit") == "yes"
    return {META_KEY: data} if data else {}


@receiver(validate_order, dispatch_uid=f"{DUID}_validate_order")
def on_validate_order(sender, positions, meta_info=None, **kwargs):
    if not is_live(sender) or not meta_info:
        return
    data = meta_info.get(META_KEY) or {}
    if data.get("choice") != CHOICE_SLOT:
        return
    if not positions_trigger(sender, positions):
        return
    if sender.settings.parentduties_confirm_checkbox and not data.get("committed", False):
        raise OrderError(_("Please confirm that you will take on the chosen parent duty."))
    try:
        check_slot_choice(sender, data.get("slots") or [])
    except SlotFull:
        raise OrderError(_("The duty you chose has just been filled up, please choose another one."))


@receiver(checkout_confirm_page_content, dispatch_uid=f"{DUID}_confirm_content")
def confirm_page_content(sender, request, **kwargs):
    if not is_live(sender):
        return ""
    from .checkoutflow import ParentDutyStep, summary_for_session
    step = ParentDutyStep(sender)
    if not step.is_applicable(request):
        return ""
    data = _session_data(request) or {}
    ctx = summary_for_session(sender, data)
    ctx.update({
        "family_known": bool(step.family_signup),
        "edit_url": step.get_step_url(request),
        "confirm_checkbox": bool(sender.settings.parentduties_confirm_checkbox) and data.get("choice") == CHOICE_SLOT,
        "request": request,
    })
    return get_template("pretix_parentduties/confirm_content.html").render(ctx, request=request)


# -------------------------------------------------------------- lifecycle

@receiver(order_placed, dispatch_uid=f"{DUID}_order_placed")
def on_order_placed(sender, order, **kwargs):
    data = (order.meta_info_data or {}).get(META_KEY)
    create_signups_for_order(order, data)


@receiver(order_canceled, dispatch_uid=f"{DUID}_order_canceled")
def on_order_canceled(sender, order, **kwargs):
    cancel_order_signups(order)


@receiver(order_expired, dispatch_uid=f"{DUID}_order_expired")
def on_order_expired(sender, order, **kwargs):
    cancel_order_signups(order)


@receiver(order_reactivated, dispatch_uid=f"{DUID}_order_reactivated")
def on_order_reactivated(sender, order, **kwargs):
    reactivate_order_signups(order)


@receiver(order_split, dispatch_uid=f"{DUID}_order_split")
def on_order_split(sender, original, split_order, **kwargs):
    handle_order_split(original, split_order)


@receiver(order_changed, dispatch_uid=f"{DUID}_order_changed")
def on_order_changed(sender, order, **kwargs):
    handle_order_changed(order)


@receiver(order_gracefully_delete, dispatch_uid=f"{DUID}_order_delete")
def on_order_gracefully_delete(sender, order, **kwargs):
    delete_order_signups(order)


# ----------------------------------------------------------- notifications

@receiver(register_notification_types, dispatch_uid=f"{DUID}_notification_types")
def register_notifications(sender, **kwargs):
    return [
        ParametrizedOrderNotificationType(
            sender,
            "pretix_parentduties.signup.overbooked",
            _("Parent duty overbooked"),
            _("Order {order.code}: the chosen parent duty was already full"),
        ),
    ]


# ---------------------------------------------------------------- products

@receiver(item_forms, dispatch_uid=f"{DUID}_item_forms")
def add_item_form(sender, request, item, **kwargs):
    from .forms import ItemDutyConfigForm
    try:
        instance = item.parentduty_config
    except ItemDutyConfig.DoesNotExist:
        instance = ItemDutyConfig(item=item)
    return ItemDutyConfigForm(
        instance=instance,
        event=sender,
        data=(request.POST if request.method == "POST" else None),
        prefix="parentduties",
    )


@receiver(item_copy_data, dispatch_uid=f"{DUID}_item_copy")
def copy_item_config(sender, source, target, **kwargs):
    try:
        cfg = source.parentduty_config
    except ItemDutyConfig.DoesNotExist:
        return
    ItemDutyConfig.objects.update_or_create(item=target, defaults={"triggers_duty": cfg.triggers_duty})


@receiver(event_copy_data, dispatch_uid=f"{DUID}_event_copy")
def copy_event_data(sender, other, item_map, **kwargs):
    copy_slots(other, sender, item_map)


# -------------------------------------------------------------- navigation

def _url(request, name, **kw):
    return reverse(f"plugins:pretix_parentduties:{name}", kwargs={
        "organizer": request.organizer.slug, "event": request.event.slug, **kw,
    })


@receiver(nav_event, dispatch_uid=f"{DUID}_nav")
def add_nav_entry(sender, request, **kwargs):
    if not has_perm(request, PERM_READ):
        return []
    url = resolve(request.path_info)
    active = url.namespace == "plugins:pretix_parentduties"
    return [{
        "label": _("Parent duties"),
        "icon": "users",
        "url": _url(request, "slots"),
        "active": active and url.url_name != "settings",
        "children": [
            {"label": _("Duty slots"), "url": _url(request, "slots"),
             "active": active and url.url_name.startswith("slot")},
            {"label": _("Signups"), "url": _url(request, "signups"),
             "active": active and url.url_name.startswith("signup")},
            {"label": _("Waitlist"), "url": _url(request, "waitlist"),
             "active": active and url.url_name.startswith("waitlist")},
            {"label": _("Products"), "url": _url(request, "items"),
             "active": active and url.url_name == "items"},
        ],
    }]


@receiver(nav_event_settings, dispatch_uid=f"{DUID}_nav_settings")
def add_settings_tab(sender, request, **kwargs):
    if not has_perm(request, "event.settings.general:write"):
        return []
    url = resolve(request.path_info)
    return [{
        "label": _("Parent duties"),
        "url": _url(request, "settings"),
        "active": url.namespace == "plugins:pretix_parentduties" and url.url_name == "settings",
    }]


# ---------------------------------------------------------------- dashboard

@receiver(event_dashboard_widgets, dispatch_uid=f"{DUID}_dashboard")
def dashboard_widget(sender, subevent=None, lazy=False, **kwargs):
    from .dashboard import widget
    return widget(sender, subevent=subevent, lazy=lazy)


# ---------------------------------------------------------------- orders

def _order_signups(order):
    return list(order.parentduty_signups.select_related("slot", "covered_by__order").order_by("-primary", "id"))


@receiver(control_order_info, dispatch_uid=f"{DUID}_control_order_info")
def control_order_box(sender, order, request, **kwargs):
    if not has_perm(request, PERM_READ):
        return ""
    signups = _order_signups(order)
    if not signups and not is_live(sender):
        return ""
    from .placeholders import duty_text
    from .services import status_label
    for s in signups:
        s.text = duty_text(s)
        s.status_text = status_label(s)
    return get_template("pretix_parentduties/control_order_info.html").render({
        "signups": signups,
        "order": order,
        "signups_url": _url(request, "signups") + f"?order={order.code}",
        "can_write": has_perm(request, PERM_WRITE),
    }, request=request)


@receiver(presale_order_info, dispatch_uid=f"{DUID}_presale_order_info")
def presale_order_box(sender, order, request=None, **kwargs):
    signups = [s for s in _order_signups(order) if s.status != DutySignup.STATUS_CANCELED]
    if not signups:
        return ""
    from .placeholders import duty_text
    from .services import status_label
    for s in signups:
        s.text = duty_text(s)
        s.status_text = status_label(s)
    from .placeholders import duty_url
    from .services import changeable_signups
    return get_template("pretix_parentduties/presale_order_info.html").render({
        "signups": signups,
        "title": sender.settings.parentduties_title,
        "duty_url": duty_url(order),
        "can_change": bool(changeable_signups(order)),
    }, request=request)


@receiver(order_api_details, dispatch_uid=f"{DUID}_api_details")
def api_order_details(sender, order, **kwargs):
    return {
        "parent_duties": [
            {
                "kind": s.kind,
                "status": s.status,
                "primary": s.primary,
                "slot": s.slot_id,
                "slot_name": str(s.slot.name) if s.slot else None,
                "covered_by_order": s.covered_by.order.code if s.covered_by_id else None,
            }
            for s in _order_signups(order)
        ]
    }


# ---------------------------------------------------------------- shredder

class ParentDutyShredder(BaseDataShredder):
    verbose_name = pgettext_lazy("shredder", "Parent duty details")
    identifier = "pretix_parentduties"
    description = _("This will remove helper names, phone numbers and notes of parent duties.")

    def generate_files(self) -> List[Tuple[str, str, str]]:
        data = {}
        for s in DutySignup.objects.filter(event=self.event).select_related("order"):
            data.setdefault(s.order.code, []).append(
                {"helper_name": s.helper_name, "helper_phone": s.helper_phone, "note": s.note}
            )
        yield "parent-duties.json", "application/json", json.dumps(data, cls=CustomJSONEncoder, indent=4)

    def shred_data(self, progress_callback=None):
        DutySignup.objects.filter(event=self.event).update(helper_name="", helper_phone="", note="")
        # The checkout choice is also kept in the order's meta_info.
        for order in self.event.orders.filter(meta_info__contains=META_KEY):
            d = order.meta_info_data or {}
            if META_KEY in d:
                for k in ("helper_name", "helper_phone", "note"):
                    d[META_KEY].pop(k, None)
                order.meta_info = json.dumps(d)
                order.save(update_fields=["meta_info"])


@receiver(register_data_shredders, dispatch_uid=f"{DUID}_shredders")
def register_shredder(sender, **kwargs):
    return ParentDutyShredder
