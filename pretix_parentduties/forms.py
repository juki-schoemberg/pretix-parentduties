from django import forms
from django.utils.translation import gettext_lazy as _, pgettext_lazy

from pretix.base.forms import I18nModelForm, SettingsForm
from pretix.base.reldate import RelativeDateTimeField
from i18nfield.forms import I18nFormField, I18nTextarea, I18nTextInput

from . import conf
from .models import DutySignup, DutySlot, ItemDutyConfig
from .services import (
    CHOICE_CANNOT_HELP, CHOICE_SIBLING, CHOICE_SKIP, CHOICE_SLOT, max_duties, waitlist_active,
)

ASK_CHOICES = (
    (conf.ASK_OFF, pgettext_lazy("parentduties_ask", "Don't ask")),
    (conf.ASK_OPTIONAL, pgettext_lazy("parentduties_ask", "Optional")),
    (conf.ASK_REQUIRED, pgettext_lazy("parentduties_ask", "Required")),
)


# ------------------------------------------------------------- checkout

class DutyChoiceForm(forms.Form):
    """The form of the checkout step.

    The ``duty`` field holds slot IDs and the alternatives (``sibling``,
    ``cannot_help``, ``skip``) in one list, rendered as radio buttons, or as
    checkboxes if several duties per family are allowed.
    """

    def __init__(self, *args, event, slots, **kwargs):
        super().__init__(*args, **kwargs)
        self.event = event
        self.slots = slots
        s = event.settings
        self.multiple = bool(s.parentduties_allow_multiple)
        self.max = max_duties(event)

        choices = [(str(slot.pk), str(slot.name)) for slot in slots]
        self.alternatives = []
        if s.parentduties_sibling_code:
            self.alternatives.append((CHOICE_SIBLING, _("A sibling is already registered")))
        if s.parentduties_allow_cannot_help:
            self.alternatives.append((CHOICE_CANNOT_HELP, _("Unfortunately we cannot take on a duty")))
        if not s.parentduties_required:
            self.alternatives.append((CHOICE_SKIP, _("Decide later")))
        choices += self.alternatives

        field_cls = forms.MultipleChoiceField if self.multiple else forms.ChoiceField
        self.fields["duty"] = field_cls(
            label=_("Duty"), choices=choices, required=True,
            error_messages={"required": _("Please choose a duty.")},
        )
        self.waitlist_offered = waitlist_active(event)
        if self.waitlist_offered:
            self.fields["waitlist"] = forms.MultipleChoiceField(
                label=_("Waitlist"), required=False,
                choices=[(str(slot.pk), str(slot.name)) for slot in slots if slot.is_full()],
            )
        self.fields["sibling_code"] = forms.CharField(
            label=_("Order code of the sibling's registration"),
            help_text=_("You can find it in the confirmation email of the other registration."),
            required=False, max_length=190,
        )
        self.fields["reason"] = forms.CharField(
            label=_("Reason"), required=False, widget=forms.Textarea(attrs={"rows": 2}),
        )
        if s.parentduties_ask_name != conf.ASK_OFF:
            self.fields["helper_name"] = forms.CharField(
                label=_("Who will help?"), max_length=255,
                required=False,
                help_text=_("Name of the person who will take on the duty."),
            )
        if s.parentduties_ask_phone != conf.ASK_OFF:
            self.fields["helper_phone"] = forms.CharField(
                label=_("Phone number"), max_length=64, required=False,
                help_text=_("So we can reach you on the day of the duty."),
            )

    @property
    def slot_ids(self):
        return {str(s.pk) for s in self.slots}

    def clean(self):
        data = super().clean()
        raw = data.get("duty")
        if raw is None:
            return data
        values = raw if isinstance(raw, list) else [raw]
        alternatives = [v for v in values if v not in self.slot_ids]
        slot_values = [v for v in values if v in self.slot_ids]
        if alternatives and (slot_values or len(alternatives) > 1):
            raise forms.ValidationError(_("Please choose either duties or one of the alternatives."))
        if len(slot_values) > self.max:
            raise forms.ValidationError(
                _("You can choose at most %(max)s duties.") % {"max": self.max}
            )

        slots_by_id = {str(s.pk): s for s in self.slots}
        for v in slot_values:
            if slots_by_id[v].is_full():
                self.add_error("duty", _("The duty “%(name)s” is already full, please choose another one.")
                               % {"name": slots_by_id[v].name})

        choice = alternatives[0] if alternatives else CHOICE_SLOT
        data["choice"] = choice
        data["slots"] = [int(v) for v in slot_values]
        if data.get("waitlist") and choice != CHOICE_SLOT:
            self.add_error("waitlist", _("The waitlist is only available together with a duty you take on now."))

        s = self.event.settings
        if choice == CHOICE_SIBLING and not (data.get("sibling_code") or "").strip():
            self.add_error("sibling_code", _("Please enter the order code of the other registration."))
        if (choice == CHOICE_CANNOT_HELP and s.parentduties_cannot_help_reason_required
                and not (data.get("reason") or "").strip()):
            self.add_error("reason", _("Please tell us briefly why."))
        if choice == CHOICE_SLOT:
            if s.parentduties_ask_name == conf.ASK_REQUIRED and not (data.get("helper_name") or "").strip():
                self.add_error("helper_name", _("This field is required."))
            if s.parentduties_ask_phone == conf.ASK_REQUIRED and not (data.get("helper_phone") or "").strip():
                self.add_error("helper_phone", _("This field is required."))
        return data

    def to_session(self):
        d = self.cleaned_data
        choice = d["choice"]
        return {
            "choice": choice,
            "slots": d["slots"] if choice == CHOICE_SLOT else [],
            "sibling_code": (d.get("sibling_code") or "").strip().upper() if choice == CHOICE_SIBLING else "",
            "note": (d.get("reason") or "").strip() if choice == CHOICE_CANNOT_HELP else "",
            "helper_name": (d.get("helper_name") or "").strip() if choice == CHOICE_SLOT else "",
            "helper_phone": (d.get("helper_phone") or "").strip() if choice == CHOICE_SLOT else "",
            "waitlist": [int(v) for v in d.get("waitlist") or []] if choice == CHOICE_SLOT else [],
        }


def _validate_waitlist_settings(form, data):
    if data.get("parentduties_waitlist_mode") == conf.WAITLIST_NOTIFY and not data.get("parentduties_change_until"):
        form.add_error("parentduties_waitlist_mode", _(
            "Please set a change deadline – otherwise parents cannot switch to the free spot themselves."
        ))


class DutyChangeForm(forms.Form):
    """Self-service change of one signup on the order page (switch only, no withdrawal)."""

    def __init__(self, *args, event, signup, slots, **kwargs):
        super().__init__(*args, **kwargs)
        self.event = event
        self.signup = signup
        self.slots = slots
        s = event.settings
        self.fields["slot"] = forms.ChoiceField(
            label=_("Duty"), choices=[(str(sl.pk), str(sl.name)) for sl in slots],
            error_messages={"required": _("Please choose a duty.")},
        )
        if s.parentduties_ask_name != conf.ASK_OFF:
            self.fields["helper_name"] = forms.CharField(
                label=_("Who will help?"), max_length=255,
                required=s.parentduties_ask_name == conf.ASK_REQUIRED,
                help_text=_("Name of the person who will take on the duty."),
            )
        if s.parentduties_ask_phone != conf.ASK_OFF:
            self.fields["helper_phone"] = forms.CharField(
                label=_("Phone number"), max_length=64,
                required=s.parentduties_ask_phone == conf.ASK_REQUIRED,
                help_text=_("So we can reach you on the day of the duty."),
            )

    def clean_slot(self):
        value = self.cleaned_data["slot"]
        slot = {str(sl.pk): sl for sl in self.slots}[value]
        if slot.pk != self.signup.slot_id and slot.is_full():
            raise forms.ValidationError(
                _("The duty “%(name)s” is already full, please choose another one.") % {"name": slot.name}
            )
        return slot


# -------------------------------------------------------------- backend

class ItemDutyConfigForm(forms.ModelForm):
    """Box "Parent duty" on the product detail page (signal ``item_forms``)."""

    title = _("Parent duty")

    class Meta:
        model = ItemDutyConfig
        fields = ["triggers_duty"]

    def __init__(self, *args, event=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.event = event
        if not self.instance.pk and event is not None:
            self.initial.setdefault("triggers_duty", bool(event.settings.parentduties_new_items_trigger))

    def save(self, commit=True):
        if self.instance.item_id and self.instance.item.pk:
            return super().save(commit=commit)
        return None


class DutySlotForm(I18nModelForm):
    class Meta:
        model = DutySlot
        # ``subevent`` and ``limit_items`` are added in __init__: building their
        # querysets at import time would fail because of django-scopes.
        fields = [
            "name", "description", "tag", "date", "time_from", "time_to",
            "capacity", "active", "position",
        ]
        widgets = {
            "date": forms.DateInput(attrs={"type": "date"}),
            "time_from": forms.TimeInput(attrs={"type": "time"}),
            "time_to": forms.TimeInput(attrs={"type": "time"}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["limit_items"] = forms.ModelMultipleChoiceField(
            queryset=self.event.items.all(), required=False, widget=forms.CheckboxSelectMultiple,
            label=DutySlot._meta.get_field("limit_items").verbose_name,
            help_text=DutySlot._meta.get_field("limit_items").help_text,
            initial=list(self.instance.limit_items.all()) if self.instance.pk else [],
        )
        if self.event.has_subevents:
            self.fields["subevent"] = forms.ModelChoiceField(
                queryset=self.event.subevents.all(), required=False,
                label=DutySlot._meta.get_field("subevent").verbose_name,
                initial=self.instance.subevent,
            )

    def clean(self):
        data = super().clean()
        if data.get("time_from") and data.get("time_to") and data["time_to"] < data["time_from"]:
            self.add_error("time_to", _("The end time must not be before the start time."))
        return data

    def save(self, commit=True):
        if "subevent" in self.cleaned_data:
            self.instance.subevent = self.cleaned_data["subevent"]
        slot = super().save(commit=commit)
        if commit:
            slot.limit_items.set(self.cleaned_data.get("limit_items") or [])
        return slot


class SignupMoveForm(forms.Form):
    slot = forms.ModelChoiceField(queryset=DutySlot.objects.none(), label=_("Duty"))

    def __init__(self, *args, event, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["slot"].queryset = DutySlot.objects.filter(event=event)


class SignupAddForm(forms.Form):
    order = forms.CharField(label=_("Order code"), max_length=190)
    kind = forms.ChoiceField(
        label=_("Type"),
        choices=[(DutySignup.KIND_SLOT, _("Own duty")), (DutySignup.KIND_EXEMPT, _("Exempt"))],
    )
    slot = forms.ModelChoiceField(queryset=DutySlot.objects.none(), label=_("Duty"), required=False)

    def __init__(self, *args, event, **kwargs):
        super().__init__(*args, **kwargs)
        self.event = event
        self.fields["slot"].queryset = DutySlot.objects.filter(event=event)

    def clean_order(self):
        code = self.cleaned_data["order"].strip().upper()
        try:
            return self.event.orders.get(code=code)
        except self.event.orders.model.DoesNotExist:
            raise forms.ValidationError(_("Unknown order code."))

    def clean(self):
        data = super().clean()
        if data.get("kind") == DutySignup.KIND_SLOT and not data.get("slot"):
            self.add_error("slot", _("Please choose a duty."))
        return data


class SignupLinkForm(forms.Form):
    code = forms.CharField(label=_("Order code of the family's registration"), max_length=190)


class ParentDutySettingsForm(SettingsForm):
    parentduties_live = forms.BooleanField(
        label=_("Checkout step live"), required=False,
        help_text=_("Only turn this on once all duties are set up. Before that, nobody is asked."),
    )
    parentduties_required = forms.BooleanField(
        label=_("Step is mandatory"), required=False,
        help_text=_("If unchecked, parents can postpone their decision."),
    )
    parentduties_title = I18nFormField(label=_("Title of the step"), widget=I18nTextInput, required=True)
    parentduties_intro = I18nFormField(label=_("Introduction"), widget=I18nTextarea, required=False)
    parentduties_allow_cannot_help = forms.BooleanField(
        label=_("Offer “Unfortunately we cannot take on a duty”"), required=False,
    )
    parentduties_cannot_help_reason_required = forms.BooleanField(
        label=_("Require a reason for “cannot help”"), required=False,
    )
    parentduties_sibling_code = forms.BooleanField(
        label=_("Sibling fallback via order code"), required=False,
        help_text=_("Families with different email addresses can refer to the registration of a sibling."),
    )
    parentduties_allow_multiple = forms.BooleanField(
        label=_("Allow several duties per family"), required=False,
    )
    parentduties_max_per_family = forms.IntegerField(
        label=_("Maximum number of duties per family"), min_value=1, max_value=20, required=True,
    )
    parentduties_ask_name = forms.ChoiceField(
        label=_("Ask for the name of the helping person"), choices=ASK_CHOICES,
    )
    parentduties_ask_phone = forms.ChoiceField(label=_("Ask for a phone number"), choices=ASK_CHOICES)
    parentduties_confirm_checkbox = forms.BooleanField(
        label=_("Commitment checkbox on the confirmation page"), required=False,
    )
    parentduties_change_until = RelativeDateTimeField(
        label=_("Parents may change their duty until"), required=False,
        help_text=_("Until then, parents can switch to another duty with free spots via their order page "
                    "or the link in {parent_duty_url}. Withdrawing is not possible. "
                    "Leave empty if only admins may change duties."),
    )
    parentduties_waitlist_mode = forms.ChoiceField(
        label=_("Waitlist for full duties"),
        choices=(
            (conf.WAITLIST_OFF, _("Off")),
            (conf.WAITLIST_NOTIFY, _("Notify by e-mail, parents switch themselves")),
            (conf.WAITLIST_AUTO, _("Move automatically and notify by e-mail")),
        ),
        help_text=_("Families keep their current duty and can additionally wait for a full one. When a spot "
                    "becomes free, the longest waiting family is notified or moved. Works until the change "
                    "deadline and the start of the event. “Notify” requires a change deadline."),
    )
    parentduties_waitlist_notify_subject = I18nFormField(
        label=_("E-mail subject: spot free"), widget=I18nTextInput, required=True,
    )
    parentduties_waitlist_notify_text = I18nFormField(
        label=_("E-mail text: spot free"), widget=I18nTextarea, required=True,
        help_text=_("Available placeholders: all order placeholders such as {parent_duty} and {parent_duty_url}, "
                    "plus {parent_duty_waitlist_slot}."),
    )
    parentduties_waitlist_auto_subject = I18nFormField(
        label=_("E-mail subject: moved automatically"), widget=I18nTextInput, required=True,
    )
    parentduties_waitlist_auto_text = I18nFormField(
        label=_("E-mail text: moved automatically"), widget=I18nTextarea, required=True,
        help_text=_("Available placeholders: all order placeholders such as {parent_duty} and {parent_duty_url}, "
                    "plus {parent_duty_waitlist_slot}."),
    )
    parentduties_new_items_trigger = forms.BooleanField(
        label=_("New products trigger the parent duty"), required=False,
        help_text=_("Default for products that have not been configured explicitly."),
    )

    def clean(self):
        data = super().clean()
        _validate_waitlist_settings(self, data)
        return data
