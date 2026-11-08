import datetime
from collections import OrderedDict

from django.contrib import messages
from django.utils.functional import cached_property
from django.utils.translation import gettext_lazy as _, pgettext_lazy

from pretix.helpers.http import redirect_to_url
from pretix.presale.checkoutflow import TemplateFlowStep
from pretix.presale.views import CartMixin

from .forms import DutyChoiceForm
from .models import DutySlot
from .services import (
    CHOICE_CANNOT_HELP, CHOICE_FAMILY, CHOICE_SIBLING, CHOICE_SLOT, SESSION_KEY, available_slots, find_family_signup,
    is_live, positions_trigger,
)


class ParentDutyStep(CartMixin, TemplateFlowStep):
    """Checkout step "Parent duty": after the questions (contact e-mail), before payment."""

    priority = 60
    identifier = "parentduties"
    template_name = "pretix_parentduties/checkout_step.html"
    icon = "users"

    @property
    def label(self):
        return pgettext_lazy("checkoutflow", "Parent duty")

    # ---------------------------------------------------------------- state

    @property
    def stored(self):
        return self.cart_session.get(SESSION_KEY) or {}

    def _store(self, data):
        self.cart_session[SESSION_KEY] = data

    @cached_property
    def cart_item_ids(self):
        return {p.item_id for p in self.positions}

    @cached_property
    def cart_subevent_ids(self):
        return {p.subevent_id for p in self.positions if p.subevent_id}

    @cached_property
    def slots(self):
        return available_slots(
            self.request.event,
            item_ids=self.cart_item_ids,
            subevent_ids=self.cart_subevent_ids if self.request.event.has_subevents else None,
        )

    @cached_property
    def family_signup(self):
        return find_family_signup(self.request.event, self.cart_session.get("email"))

    # ---------------------------------------------------------------- flow

    def is_applicable(self, request):
        self.request = request
        if not is_live(request.event):
            return False
        return positions_trigger(request.event, self.positions)

    def is_completed(self, request, warn=False):
        self.request = request
        data = self.stored
        if self.family_signup:
            return True
        choice = data.get("choice")
        if not choice or choice == CHOICE_FAMILY:
            if warn:
                messages.warning(request, _("Please choose your parent duty."))
            return False
        if choice == CHOICE_SLOT:
            ok_ids = {s.pk for s in self.slots if not s.is_full()}
            chosen = data.get("slots") or []
            if not chosen or any(pk not in ok_ids for pk in chosen):
                if warn:
                    messages.warning(request, _(
                        "The duty you chose has just been filled up, please choose another one."
                    ))
                return False
        return True

    @cached_property
    def form(self):
        stored = self.stored
        initial = {}
        if stored.get("choice") == CHOICE_SLOT:
            slots = [str(pk) for pk in stored.get("slots") or []]
            initial["duty"] = slots if self.request.event.settings.parentduties_allow_multiple else (
                slots[0] if slots else None
            )
        elif stored.get("choice"):
            initial["duty"] = stored["choice"]
        initial["sibling_code"] = stored.get("sibling_code", "")
        initial["reason"] = stored.get("note", "")
        initial["helper_name"] = stored.get("helper_name", "")
        initial["helper_phone"] = stored.get("helper_phone", "")
        return DutyChoiceForm(
            data=self.request.POST if self.request.method == "POST" else None,
            event=self.request.event,
            slots=self.slots,
            initial=initial,
            prefix="parentduties",
        )

    def post(self, request):
        self.request = request
        if self.family_signup:
            self._store({"choice": CHOICE_FAMILY})
            return redirect_to_url(self.get_next_url(request))
        if not self.form.is_valid():
            messages.error(request, _("We had difficulties processing your input. Please review the errors below."))
            return self.render()
        self._store(self.form.to_session())
        return redirect_to_url(self.get_next_url(request))

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["cart"] = self.get_cart()
        ctx["family_known"] = bool(self.family_signup)
        ctx["form"] = self.form
        ctx["title"] = self.request.event.settings.parentduties_title
        ctx["intro"] = self.request.event.settings.parentduties_intro
        ctx["multiple"] = bool(self.request.event.settings.parentduties_allow_multiple)

        selected = self.form["duty"].value() or []
        if not isinstance(selected, (list, tuple)):
            selected = [selected]
        selected = {str(v) for v in selected}

        groups = OrderedDict()
        for slot in sorted(self.slots, key=slot_sort_key):
            slot.checked = str(slot.pk) in selected
            slot.free = slot.free_count()
            slot.full = slot.is_full()
            key = slot.date_display or str(_("Other duties"))
            groups.setdefault(key, []).append(slot)
        ctx["slot_groups"] = groups
        offered = {v for v, _label in self.form.alternatives}
        ctx["offer_sibling"] = CHOICE_SIBLING in offered
        ctx["offer_cannot_help"] = CHOICE_CANNOT_HELP in offered
        ctx["alternatives"] = [
            {"value": v, "label": label, "checked": v in selected} for v, label in self.form.alternatives
        ]
        return ctx


def slot_sort_key(s):
    return (
        s.date is None, s.date or datetime.date.min,
        s.time_from is None, s.time_from or datetime.time.min,
        s.position, s.pk,
    )


def summary_for_session(event, data):
    """Data for the confirmation page summary."""
    choice = data.get("choice")
    slots = []
    if choice == CHOICE_SLOT:
        slots = list(DutySlot.objects.filter(event=event, pk__in=data.get("slots") or []))
    return {"choice": choice, "slots": slots, "data": data}
