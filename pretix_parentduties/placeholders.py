"""Text placeholders (e-mails etc.) and ticket layout variables."""
import datetime

from django.dispatch import receiver
from django.utils.formats import date_format, time_format
from django.utils.translation import gettext as _, gettext_lazy, pgettext

from pretix.base.services.placeholders import SimpleFunctionalTextPlaceholder
from pretix.base.signals import layout_text_variables, register_text_placeholders

from .models import DutySignup, DutySlot
from .services import family_code, status_label

DUID = "pretix_parentduties"


def primary_signup(order):
    if order is None or not order.pk:
        return None
    signups = [s for s in order.parentduty_signups.all() if s.status != DutySignup.STATUS_CANCELED]
    for s in signups:
        if s.primary:
            return s
    return signups[0] if signups else None


def with_clock(text):
    """Append "Uhr" in German. English stays as is."""
    if not text:
        return ""
    return pgettext("parentduties time display", "%(time)s") % {"time": text}


def _slot_text(slot):
    when = ", ".join(p for p in (slot.date_display, with_clock(slot.time_display)) if p)
    return ", ".join(p for p in (str(slot.name), when) if p)


def duty_text(signup):
    if signup is None:
        return ""
    if signup.kind == DutySignup.KIND_SLOT:
        if signup.slot:
            return _slot_text(signup.slot)
        return _("not yet assigned")
    if signup.kind == DutySignup.KIND_SIBLING:
        code = family_code(signup)
        if code:
            return _("covered by your registration %(code)s") % {"code": code}
        return _("covered by a sibling's registration (under review)")
    if signup.kind == DutySignup.KIND_EXEMPT:
        return _("exempt")
    if signup.kind == DutySignup.KIND_CANNOT_HELP:
        return _("no duty")
    return ""


def _slot_attr(func):
    def inner(signup):
        if signup is None:
            return ""
        root = signup.root() if signup.kind == DutySignup.KIND_SIBLING else signup
        slot = root.slot if root.kind == DutySignup.KIND_SLOT else None
        if slot is None:
            return ""
        return func(slot) or ""
    return inner


def _time(slot):
    return with_clock(slot.time_display)


ORDER_VALUES = {
    "parent_duty": (gettext_lazy("Parent duty of the order"), duty_text,
                    lambda: _("Cake Monday, 07/13/2027, 14:00–16:00")),
    "parent_duty_name": (gettext_lazy("Name of the parent duty"), _slot_attr(lambda s: str(s.name)),
                         lambda: _("Cake Monday")),
    "parent_duty_description": (gettext_lazy("Description of the parent duty"),
                                _slot_attr(lambda s: str(s.description or "")),
                                lambda: _("Please drop it off at the kitchen by 13:30")),
    "parent_duty_category": (gettext_lazy("Category of the parent duty"), _slot_attr(lambda s: s.tag),
                             lambda: _("Cake")),
    "parent_duty_date": (gettext_lazy("Date of the parent duty"), _slot_attr(lambda s: s.date_display),
                         lambda: date_format(datetime.date(2027, 7, 13), "SHORT_DATE_FORMAT")),
    "parent_duty_time_from": (gettext_lazy("Start time of the parent duty"),
                              _slot_attr(lambda s: time_format(s.time_from, "H:i") if s.time_from else ""),
                              lambda: "14:00"),
    "parent_duty_time_to": (gettext_lazy("End time of the parent duty"),
                            _slot_attr(lambda s: time_format(s.time_to, "H:i") if s.time_to else ""),
                            lambda: "16:00"),
    "parent_duty_time": (gettext_lazy("Time of the parent duty"), _slot_attr(_time),
                         lambda: "14:00–16:00"),
    "parent_duty_helper_name": (gettext_lazy("Helper of the parent duty"),
                                lambda s: s.helper_name if s else "", lambda: "Anna Muster"),
    "parent_duty_helper_phone": (gettext_lazy("Helper phone of the parent duty"),
                                 lambda s: s.helper_phone if s else "", lambda: "0151 23456789"),
    "parent_duty_status": (gettext_lazy("Status of the parent duty"), status_label, lambda: _("committed")),
    "parent_duty_family_code": (gettext_lazy("Order code carrying the family's duty"), family_code,
                                lambda: "ABC12"),
    "parent_duty_note": (gettext_lazy("Note on the parent duty"), lambda s: s.note if s else "",
                         lambda: _("We'll bring a gluten-free cake.")),
}


def duty_all_text(order):
    if order is None or not order.pk:
        return ""
    lines = [
        duty_text(s) for s in order.parentduty_signups.all()
        if s.status != DutySignup.STATUS_CANCELED
    ]
    return "\n".join(line for line in lines if line)


def free_spots(event):
    total = 0
    for slot in DutySlot.objects.filter(event=event, active=True).with_occupancy():
        free = slot.free_count()
        if free:
            total += free
    return str(total)


def open_slots(event):
    lines = []
    for slot in DutySlot.objects.filter(event=event, active=True).with_occupancy():
        free = slot.free_count()
        if free is None:
            lines.append(slot.full_display)
        elif free > 0:
            lines.append("{} ({})".format(slot.full_display, _("%(n)s free") % {"n": free}))
    return "\n".join(lines)


def order_value(name, order):
    """Render one of the order placeholders as string (never ``None``)."""
    if name == "parent_duty_all":
        return duty_all_text(order)
    func = ORDER_VALUES[name][1]
    try:
        value = func(primary_signup(order))
    except DutySlot.DoesNotExist:
        value = ""
    return "" if value is None else str(value)


@receiver(register_text_placeholders, dispatch_uid=f"{DUID}_placeholders")
def register_placeholders(sender, **kwargs):
    ph = []
    for name in list(ORDER_VALUES) + ["parent_duty_all"]:
        sample = ORDER_VALUES[name][2] if name in ORDER_VALUES else (
            lambda: _("Cake Monday, 07/13/2027, 14:00–16:00") + "\n" + _("Set-up Friday, 07/09/2027")
        )
        ph.append(SimpleFunctionalTextPlaceholder(
            name, ["order"],
            (lambda n: lambda order: order_value(n, order))(name),
            (lambda s: lambda event: str(s()))(sample),
        ))
    ph.append(SimpleFunctionalTextPlaceholder(
        "parent_duty_free_spots", ["event"], free_spots, lambda event: "12",
    ))
    ph.append(SimpleFunctionalTextPlaceholder(
        "parent_duty_open_slots", ["event"], open_slots,
        lambda event: _("Cake Monday, 07/13/2027 (2 free)"),
    ))
    return ph


@receiver(layout_text_variables, dispatch_uid=f"{DUID}_layout_variables")
def register_layout_variables(sender, **kwargs):
    variables = {}
    for name in list(ORDER_VALUES) + ["parent_duty_all"]:
        label = ORDER_VALUES[name][0] if name in ORDER_VALUES else gettext_lazy("All parent duties of the order")
        sample = ORDER_VALUES[name][2] if name in ORDER_VALUES else (lambda: _("Cake Monday"))
        variables[f"parentduties_{name}"] = {
            "label": label,
            "editor_sample": sample(),
            "evaluate": (lambda n: lambda op, order, event: order_value(n, order))(name),
        }
    return variables
