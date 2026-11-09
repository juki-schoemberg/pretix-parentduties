from django.contrib import messages
from django.db import transaction
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse
from django.utils.functional import cached_property
from django.utils.http import url_has_allowed_host_and_scheme
from django.utils.translation import gettext_lazy as _
from django.views import View
from django.views.generic import FormView, ListView, TemplateView

from pretix.base.models import Event, Item
from pretix.control.permissions import EventPermissionRequiredMixin
from pretix.control.views.event import EventSettingsFormView, EventSettingsViewMixin
from pretix.presale.views import EventViewMixin
from pretix.presale.views.order import OrderDetailMixin

from . import services
from .forms import (
    DutyChangeForm, DutySlotForm, ParentDutySettingsForm, SignupAddForm, SignupLinkForm, SignupMoveForm,
)
from .models import DutySignup, DutySlot, DutyWaitlistEntry, ItemDutyConfig
from .signals import PERM_READ, PERM_WRITE, has_perm


class PluginViewMixin:
    def url(self, name, **kw):
        return reverse(f"plugins:pretix_parentduties:{name}", kwargs={
            "organizer": self.request.organizer.slug, "event": self.request.event.slug, **kw,
        })

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["can_write"] = has_perm(self.request, PERM_WRITE)
        ctx["live"] = services.is_live(self.request.event)
        ctx["settings_url"] = self.url("settings")
        return ctx


# -------------------------------------------------------------------- slots

class SlotListView(PluginViewMixin, EventPermissionRequiredMixin, TemplateView):
    template_name = "pretix_parentduties/slots.html"
    permission = PERM_READ

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        slots = list(DutySlot.objects.filter(event=self.request.event).with_occupancy().with_waiting().order_by(
            "date", "time_from", "position", "id"
        ))
        for s in slots:
            s.free = s.free_count()
            s.percent = min(100, int(s.occupied * 100 / s.capacity)) if s.capacity else (100 if s.occupied else 0)
        ctx["slots"] = slots
        ctx["review_count"] = DutySignup.objects.filter(
            event=self.request.event, status=DutySignup.STATUS_NEEDS_REVIEW,
        ).count()
        return ctx


class SlotEditView(PluginViewMixin, EventPermissionRequiredMixin, FormView):
    template_name = "pretix_parentduties/slot_form.html"
    permission = PERM_WRITE
    form_class = DutySlotForm

    def get_object(self):
        if "pk" in self.kwargs:
            return get_object_or_404(DutySlot, event=self.request.event, pk=self.kwargs["pk"])
        return DutySlot(event=self.request.event)

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["instance"] = self.get_object()
        kwargs["event"] = self.request.event
        return kwargs

    @transaction.atomic
    def form_valid(self, form):
        created = not form.instance.pk
        form.instance.event = self.request.event
        slot = form.save()
        transaction.on_commit(lambda: services.process_waitlist(slot))
        self.request.event.log_action(
            "pretix_parentduties.slot.created" if created else "pretix_parentduties.slot.changed",
            user=self.request.user, data={"id": slot.pk, "name": str(slot.name)},
        )
        messages.success(self.request, _("The duty slot has been saved."))
        return redirect(self.url("slots"))


class SlotDuplicateView(PluginViewMixin, EventPermissionRequiredMixin, View):
    permission = PERM_WRITE

    @transaction.atomic
    def post(self, request, *args, **kwargs):
        slot = get_object_or_404(DutySlot, event=request.event, pk=kwargs["pk"])
        items = list(slot.limit_items.all())
        slot.pk = slot.id = None
        slot.position += 1
        slot.save()
        slot.limit_items.set(items)
        request.event.log_action("pretix_parentduties.slot.created", user=request.user,
                                 data={"id": slot.pk, "name": str(slot.name)})
        messages.success(request, _("The duty slot has been duplicated."))
        return redirect(self.url("slot.edit", pk=slot.pk))


class SlotDeleteView(PluginViewMixin, EventPermissionRequiredMixin, View):
    permission = PERM_WRITE

    @transaction.atomic
    def post(self, request, *args, **kwargs):
        slot = get_object_or_404(DutySlot, event=request.event, pk=kwargs["pk"])
        if slot.signups.exists():
            slot.active = False
            slot.save(update_fields=["active"])
            messages.warning(request, _(
                "This duty slot already has signups and has therefore only been deactivated."
            ))
        else:
            request.event.log_action("pretix_parentduties.slot.deleted", user=request.user,
                                     data={"id": slot.pk, "name": str(slot.name)})
            slot.delete()
            messages.success(request, _("The duty slot has been deleted."))
        return redirect(self.url("slots"))


# ------------------------------------------------------------------ signups

class SignupListView(PluginViewMixin, EventPermissionRequiredMixin, ListView):
    template_name = "pretix_parentduties/signups.html"
    permission = PERM_READ
    context_object_name = "signups"
    paginate_by = 100

    def get_queryset(self):
        qs = DutySignup.objects.filter(event=self.request.event).select_related(
            "order", "slot", "covered_by__order",
        ).prefetch_related("order__positions").order_by("slot__date", "slot__time_from", "slot__position", "id")
        g = self.request.GET
        if g.get("slot"):
            qs = qs.filter(slot_id=g["slot"])
        if g.get("tag"):
            qs = qs.filter(slot__tag=g["tag"])
        if g.get("status"):
            qs = qs.filter(status=g["status"])
        if g.get("kind"):
            qs = qs.filter(kind=g["kind"])
        if g.get("order"):
            qs = qs.filter(order__code__iexact=g["order"].strip())
        if g.get("review"):
            qs = qs.filter(
                Q(status=DutySignup.STATUS_NEEDS_REVIEW)
                | Q(kind=DutySignup.KIND_CANNOT_HELP, status=DutySignup.STATUS_ACTIVE)
            )
        if not g.get("status") and not g.get("order"):
            qs = qs.exclude(status=DutySignup.STATUS_CANCELED)
        return qs

    def get_context_data(self, **kwargs):
        from .placeholders import duty_text
        ctx = super().get_context_data(**kwargs)
        for s in ctx["signups"]:
            s.text = duty_text(s)
            s.status_text = services.status_label(s)
            s.children = [p.attendee_name for p in s.order.positions.all() if p.attendee_name]
        ctx["slots"] = DutySlot.objects.filter(event=self.request.event)
        ctx["tags"] = sorted(set(
            DutySlot.objects.filter(event=self.request.event).exclude(tag="").values_list("tag", flat=True)
        ))
        ctx["status_choices"] = DutySignup.STATUS_CHOICES
        ctx["kind_choices"] = DutySignup.KIND_CHOICES
        ctx["filters"] = self.request.GET
        ctx["add_form"] = SignupAddForm(event=self.request.event)
        return ctx


class SignupActionView(PluginViewMixin, EventPermissionRequiredMixin, View):
    permission = PERM_WRITE

    @transaction.atomic
    def post(self, request, *args, **kwargs):
        signup = get_object_or_404(DutySignup, event=request.event, pk=kwargs["pk"])
        action = request.POST.get("action")
        user = request.user
        if action == "move":
            form = SignupMoveForm(request.POST, event=request.event)
            if form.is_valid():
                slot = form.cleaned_data["slot"]
                if slot.is_full():
                    messages.warning(request, _("Note: the capacity of this duty is now exceeded."))
                services.admin_move(signup, slot, user=user)
            else:
                messages.error(request, _("Please choose a duty."))
        elif action == "cancel":
            services.admin_cancel(signup, user=user)
        elif action == "exempt":
            services.admin_set(signup, DutySignup.KIND_EXEMPT, DutySignup.STATUS_ACTIVE, user=user)
        elif action == "confirm":
            if signup.kind == DutySignup.KIND_SLOT and not signup.slot_id:
                messages.error(request, _("Please assign a duty first."))
            else:
                services.admin_set(signup, signup.kind, DutySignup.STATUS_ACTIVE, user=user,
                                   covered_by=signup.covered_by)
        elif action == "link":
            form = SignupLinkForm(request.POST)
            root = services.find_signup_by_code(request.event, form.data.get("code"), exclude_order=signup.order) \
                if form.is_valid() else None
            if root:
                services.admin_set(signup, DutySignup.KIND_SIBLING, DutySignup.STATUS_ACTIVE, user=user,
                                   covered_by=root)
            else:
                messages.error(request, _("No active parent duty found for this order code."))
        elif action == "unlink":
            services.admin_set(signup, DutySignup.KIND_SLOT, DutySignup.STATUS_NEEDS_REVIEW, user=user)
        else:
            messages.error(request, _("Unknown action."))
            return redirect(self.url("signups"))
        messages.success(request, _("Your changes have been saved."))
        nxt = request.POST.get("next") or ""
        if not url_has_allowed_host_and_scheme(nxt, allowed_hosts={request.get_host()}):
            nxt = self.url("signups")
        return redirect(nxt)


class SignupAddView(PluginViewMixin, EventPermissionRequiredMixin, View):
    permission = PERM_WRITE

    @transaction.atomic
    def post(self, request, *args, **kwargs):
        form = SignupAddForm(request.POST, event=request.event)
        if not form.is_valid():
            for errors in form.errors.values():
                for e in errors:
                    messages.error(request, e)
            return redirect(self.url("signups"))
        services.admin_add(form.cleaned_data["order"], slot=form.cleaned_data.get("slot"),
                           kind=form.cleaned_data["kind"], user=request.user)
        messages.success(request, _("The parent duty has been added."))
        return redirect(self.url("signups") + "?order=" + form.cleaned_data["order"].code)


class WaitlistView(PluginViewMixin, EventPermissionRequiredMixin, ListView):
    template_name = "pretix_parentduties/waitlist.html"
    permission = PERM_READ
    context_object_name = "entries"
    paginate_by = 100

    def get_queryset(self):
        qs = DutyWaitlistEntry.objects.filter(event=self.request.event).select_related(
            "slot", "signup__order", "signup__slot",
        ).order_by("slot__date", "slot__time_from", "slot__position", "slot_id", "created", "id")
        status = self.request.GET.get("status", DutyWaitlistEntry.STATUS_WAITING)
        if status:
            qs = qs.filter(status=status)
        if self.request.GET.get("slot"):
            qs = qs.filter(slot_id=self.request.GET["slot"])
        return qs

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["slots"] = DutySlot.objects.filter(event=self.request.event)
        ctx["status_choices"] = DutyWaitlistEntry.STATUS_CHOICES
        ctx["status"] = self.request.GET.get("status", DutyWaitlistEntry.STATUS_WAITING)
        ctx["filters"] = self.request.GET
        ctx["mode"] = services.waitlist_mode(self.request.event)
        ctx["active"] = services.waitlist_active(self.request.event)
        return ctx


class WaitlistRemoveView(PluginViewMixin, EventPermissionRequiredMixin, View):
    permission = PERM_WRITE

    @transaction.atomic
    def post(self, request, *args, **kwargs):
        entry = get_object_or_404(
            DutyWaitlistEntry, event=request.event, pk=kwargs["pk"], status=DutyWaitlistEntry.STATUS_WAITING,
        )
        services.leave_waitlist(entry, user=request.user)
        messages.success(request, _("The waitlist entry has been removed."))
        return redirect(self.url("waitlist"))


# ----------------------------------------------------------------- products

class ItemsView(PluginViewMixin, EventPermissionRequiredMixin, TemplateView):
    """Overview of all products with the "triggers duty" switch."""

    template_name = "pretix_parentduties/items.html"
    permission = PERM_READ

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ids = services.triggering_item_ids(self.request.event)
        configured = set(
            ItemDutyConfig.objects.filter(item__event=self.request.event).values_list("item_id", flat=True)
        )
        items = list(Item.objects.filter(event=self.request.event).select_related("category").order_by(
            "category__position", "position", "id"
        ))
        for i in items:
            i.triggers = i.pk in ids
            i.configured = i.pk in configured
        ctx["items"] = items
        ctx["default"] = bool(self.request.event.settings.parentduties_new_items_trigger)
        return ctx

    @transaction.atomic
    def post(self, request, *args, **kwargs):
        if not has_perm(request, PERM_WRITE):
            messages.error(request, _("You do not have permission to change this."))
            return redirect(self.url("items"))
        selected = set(request.POST.getlist("items"))
        changed = {}
        for item in Item.objects.filter(event=request.event):
            value = str(item.pk) in selected
            cfg, created = ItemDutyConfig.objects.get_or_create(item=item, defaults={"triggers_duty": value})
            if not created and cfg.triggers_duty != value:
                cfg.triggers_duty = value
                cfg.save(update_fields=["triggers_duty"])
                changed[item.pk] = value
            elif created:
                changed[item.pk] = value
        if changed:
            request.event.log_action("pretix_parentduties.items.changed", user=request.user,
                                     data={"items": changed})
        messages.success(request, _("Your changes have been saved."))
        return redirect(self.url("items"))


# ----------------------------------------------------------------- reminder

class ReminderCreateView(PluginViewMixin, EventPermissionRequiredMixin, View):
    """Creates the ready-made reminder e-mail rule and opens it in pretix' rule editor."""

    permission = "event.settings.general:write"

    @transaction.atomic
    def post(self, request, *args, **kwargs):
        from .reminder import NoTriggeringProducts, create_reminder_rule
        try:
            days = max(0, min(365, int(request.POST.get("days", 7))))
        except ValueError:
            days = 7
        try:
            rule = create_reminder_rule(request.event, days_before=days, user=request.user)
        except NoTriggeringProducts:
            messages.error(request, _("Please choose the products that trigger the parent duty first."))
            return redirect(self.url("settings"))
        messages.success(request, _(
            "The reminder e-mail has been created as a scheduled e-mail. Check the text and adjust it if needed."
        ))
        return redirect(reverse("plugins:sendmail:rule.update", kwargs={
            "organizer": request.organizer.slug, "event": request.event.slug, "rule": rule.pk,
        }))


# ----------------------------------------------------------------- settings

class SettingsView(EventSettingsViewMixin, EventSettingsFormView):
    model = Event
    form_class = ParentDutySettingsForm
    template_name = "pretix_parentduties/settings.html"
    permission = "event.settings.general:write"

    def get_success_url(self):
        return reverse("plugins:pretix_parentduties:settings", kwargs={
            "organizer": self.request.event.organizer.slug, "event": self.request.event.slug,
        })


# ------------------------------------------------------------------ presale

class OrderDutyView(EventViewMixin, OrderDetailMixin, TemplateView):
    """The parents' page for their duty, linked from the order page and via ``{parent_duty_url}``.

    Shows the duty; until the configured deadline it can be switched to another slot
    with free spots. Withdrawing is not possible.
    """

    template_name = "pretix_parentduties/order_duty.html"

    @cached_property
    def changeable(self):
        return services.changeable_signups(self.order)

    def slots_for(self, signup):
        order = self.order
        positions = list(order.positions.all())
        slots = services.available_slots(
            order.event,
            item_ids={p.item_id for p in positions},
            subevent_ids={p.subevent_id for p in positions if p.subevent_id} if order.event.has_subevents else None,
        )
        taken = {s.slot_id for s in self.changeable if s.pk != signup.pk and s.slot_id}
        return [sl for sl in slots if sl.pk not in taken]

    def forms(self, data=None, only=None):
        result = []
        for signup in self.changeable:
            bound = data if only == signup.pk else None
            result.append(DutyChangeForm(
                data=bound, event=self.request.event, signup=signup, slots=self.slots_for(signup),
                prefix=f"duty-{signup.pk}",
                initial={"slot": str(signup.slot_id) if signup.slot_id else None,
                         "helper_name": signup.helper_name, "helper_phone": signup.helper_phone},
            ))
        return result

    def get(self, request, *args, **kwargs):
        return self.render(self.forms())

    def _own_signup(self, signup_id):
        return self.order.parentduty_signups.filter(pk=signup_id).exclude(
            status=DutySignup.STATUS_CANCELED,
        ).select_related("slot").first()

    def post_waitlist(self, request, signup_id):
        action = request.POST.get("action")
        if action == "leave_waitlist":
            entry = DutyWaitlistEntry.objects.filter(
                pk=request.POST.get("entry") or 0, signup__order=self.order,
                status=DutyWaitlistEntry.STATUS_WAITING,
            ).select_related("signup__order", "slot").first()
            if entry:
                services.leave_waitlist(entry)
                messages.success(request, _("You have been removed from the waitlist."))
            return redirect(self.request.path)
        signup = self._own_signup(signup_id)
        slot = DutySlot.objects.filter(event=request.event, pk=request.POST.get("waitlist_slot") or 0).first()
        if signup is None or slot is None or not services.can_join_waitlist(signup, slot):
            messages.error(request, _("You cannot join the waitlist for this duty."))
        else:
            services.join_waitlist(signup, slot)
            messages.success(request, _("You are on the waitlist. We will let you know when a spot becomes free."))
        return redirect(self.request.path)

    def post(self, request, *args, **kwargs):
        try:
            signup_id = int(request.POST.get("signup", ""))
        except ValueError:
            signup_id = None
        if request.POST.get("action") in ("join_waitlist", "leave_waitlist"):
            return self.post_waitlist(request, signup_id)
        forms = self.forms(data=request.POST, only=signup_id)
        form = next((f for f in forms if f.signup.pk == signup_id), None)
        if form is None:
            messages.error(request, _("Your duty can no longer be changed."))
            return redirect(self.request.path)
        if form.is_valid():
            try:
                services.customer_change(
                    form.signup, form.cleaned_data["slot"],
                    helper_name=form.cleaned_data.get("helper_name"),
                    helper_phone=form.cleaned_data.get("helper_phone"),
                )
            except services.SlotFull:
                messages.error(request, _("The duty you chose has just been filled up, please choose another one."))
            else:
                messages.success(request, _("Your parent duty has been changed. Thank you!"))
                return redirect(self.request.path)
        return self.render(forms)

    def render(self, forms):
        from .placeholders import duty_text
        signups = [
            s for s in self.order.parentduty_signups.select_related(
                "slot", "covered_by__order",
            ).order_by("-primary", "id")
            if s.status != DutySignup.STATUS_CANCELED
        ]
        forms_by_signup = {f.signup.pk: f for f in forms}
        waitlist_on = services.waitlist_active(self.request.event)
        for s in signups:
            s.waiting = list(s.waitlist_entries.filter(
                status=DutyWaitlistEntry.STATUS_WAITING,
            ).select_related("slot"))
            waiting_ids = {e.slot_id for e in s.waiting}
            s.joinable = [
                sl for sl in DutySlot.objects.filter(event=self.request.event, active=True).with_occupancy()
                if sl.pk not in waiting_ids and services.can_join_waitlist(s, sl)
            ] if waitlist_on and s.kind == DutySignup.KIND_SLOT else []
            s.text = duty_text(s)
            s.status_text = services.status_label(s)
            s.form = forms_by_signup.get(s.pk)
            if s.form is not None:
                for sl in s.form.slots:
                    sl.free = sl.free_count()
                    sl.full = sl.is_full() and sl.pk != s.slot_id
                    sl.checked = str(sl.pk) == str(s.form["slot"].value())
        return self.render_to_response(self.get_context_data(
            order=self.order,
            signups=signups,
            deadline=services.change_deadline(self.request.event),
            can_change=bool(forms),
            title=self.request.event.settings.parentduties_title,
            order_url=self.get_order_url(),
        ))
