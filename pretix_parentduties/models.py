from django.db import models
from django.db.models import Count, Q
from django.utils.formats import date_format, time_format
from django.utils.translation import gettext_lazy as _, pgettext_lazy

from i18nfield.fields import I18nCharField, I18nTextField


class DutySlotQuerySet(models.QuerySet):
    def with_occupancy(self):
        return self.annotate(
            occupied=Count(
                "signups",
                filter=Q(
                    signups__kind=DutySignup.KIND_SLOT,
                    signups__status=DutySignup.STATUS_ACTIVE,
                ),
                distinct=True,
            )
        )

    def with_waiting(self):
        return self.annotate(
            waiting=Count(
                "waitlist_entries",
                filter=Q(waitlist_entries__status=DutyWaitlistEntry.STATUS_WAITING),
                distinct=True,
            )
        )


class DutySlot(models.Model):
    """A concrete duty that families can sign up for, e.g. "Cake Monday"."""

    event = models.ForeignKey(
        "pretixbase.Event", on_delete=models.CASCADE, related_name="parentduty_slots",
    )
    subevent = models.ForeignKey(
        "pretixbase.SubEvent", on_delete=models.CASCADE, null=True, blank=True,
        related_name="parentduty_slots", verbose_name=pgettext_lazy("subevent", "Date"),
    )
    name = I18nCharField(max_length=190, verbose_name=_("Name"))
    description = I18nTextField(
        null=True, blank=True, verbose_name=_("Description"),
        help_text=_("Details such as meeting point or what to bring."),
    )
    tag = models.CharField(
        max_length=190, blank=True, verbose_name=_("Category"),
        help_text=_("Free grouping such as “Set-up” or “Cake”, used for filters and exports."),
    )
    date = models.DateField(null=True, blank=True, verbose_name=_("Date"))
    time_from = models.TimeField(null=True, blank=True, verbose_name=_("Start time"))
    time_to = models.TimeField(null=True, blank=True, verbose_name=_("End time"))
    capacity = models.PositiveIntegerField(
        null=True, blank=True, verbose_name=_("Capacity"),
        help_text=_("Leave empty for unlimited."),
    )
    active = models.BooleanField(default=True, verbose_name=_("Active"))
    position = models.IntegerField(default=0, verbose_name=_("Position"))
    limit_items = models.ManyToManyField(
        "pretixbase.Item", blank=True, related_name="+",
        verbose_name=_("Only offer for these products"),
        help_text=_("Leave empty to offer this duty for all products that trigger the parent duty."),
    )

    objects = DutySlotQuerySet.as_manager()

    class Meta:
        ordering = ("position", "date", "time_from", "id")

    def __str__(self):
        return str(self.name)

    @property
    def time_display(self):
        if self.time_from and self.time_to:
            return "{}–{}".format(time_format(self.time_from, "H:i"), time_format(self.time_to, "H:i"))
        if self.time_from:
            return time_format(self.time_from, "H:i")
        return ""

    @property
    def date_display(self):
        return date_format(self.date, "SHORT_DATE_FORMAT") if self.date else ""

    @property
    def full_display(self):
        parts = [str(self.name)]
        when = ", ".join(p for p in (self.date_display, self.time_display) if p)
        if when:
            parts.append(when)
        return ", ".join(parts)

    def occupied_count(self):
        if hasattr(self, "occupied"):
            return self.occupied
        return self.signups.filter(
            kind=DutySignup.KIND_SLOT, status=DutySignup.STATUS_ACTIVE,
        ).count()

    def free_count(self):
        """Free spots, or ``None`` for unlimited capacity."""
        if self.capacity is None:
            return None
        return max(0, self.capacity - self.occupied_count())

    def is_full(self):
        free = self.free_count()
        return free is not None and free <= 0


class DutySignup(models.Model):
    """The duty commitment of one order."""

    KIND_SLOT = "slot"
    KIND_SIBLING = "sibling"
    KIND_EXEMPT = "exempt"
    KIND_CANNOT_HELP = "cannot_help"
    KIND_CHOICES = (
        (KIND_SLOT, _("Own duty")),
        (KIND_SIBLING, _("Covered by sibling")),
        (KIND_EXEMPT, _("Exempt")),
        (KIND_CANNOT_HELP, _("Cannot help")),
    )

    STATUS_ACTIVE = "active"
    STATUS_NEEDS_REVIEW = "needs_review"
    STATUS_CANCELED = "canceled"
    STATUS_CHOICES = (
        (STATUS_ACTIVE, _("Active")),
        (STATUS_NEEDS_REVIEW, _("Needs review")),
        (STATUS_CANCELED, _("Canceled")),
    )

    event = models.ForeignKey(
        "pretixbase.Event", on_delete=models.CASCADE, related_name="parentduty_signups",
    )
    order = models.ForeignKey(
        "pretixbase.Order", on_delete=models.CASCADE, related_name="parentduty_signups",
    )
    primary = models.BooleanField(
        default=True,
        help_text="The mandatory signup of the order. Additional voluntary duties have primary=False.",
    )
    kind = models.CharField(max_length=20, choices=KIND_CHOICES, verbose_name=_("Type"))
    slot = models.ForeignKey(
        DutySlot, on_delete=models.PROTECT, null=True, blank=True, related_name="signups",
        verbose_name=_("Duty"),
    )
    covered_by = models.ForeignKey(
        "self", on_delete=models.SET_NULL, null=True, blank=True, related_name="covers",
    )
    sibling_code = models.CharField(
        max_length=190, blank=True,
        help_text="Order code entered by the parents for the sibling fallback.",
    )
    email_normalized = models.CharField(max_length=254, blank=True, db_index=True)
    helper_name = models.CharField(max_length=255, blank=True, verbose_name=_("Helper name"))
    helper_phone = models.CharField(max_length=64, blank=True, verbose_name=_("Helper phone"))
    status = models.CharField(
        max_length=20, choices=STATUS_CHOICES, default=STATUS_ACTIVE, verbose_name=_("Status"),
    )
    note = models.TextField(blank=True, verbose_name=_("Note"))
    created = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("-primary", "id")
        indexes = [
            models.Index(fields=["event", "email_normalized"]),
            models.Index(fields=["event", "status"]),
        ]

    def __str__(self):
        return "{} ({})".format(self.order.code, self.get_kind_display())

    def root(self):
        """Follow ``covered_by`` to the signup that actually carries the duty."""
        seen = set()
        cur = self
        while cur.kind == self.KIND_SIBLING and cur.covered_by_id and cur.pk not in seen:
            seen.add(cur.pk)
            cur = cur.covered_by
        return cur

    @property
    def is_fulfilling(self):
        return self.status == self.STATUS_ACTIVE and self.kind in (self.KIND_SLOT, self.KIND_EXEMPT)


class ItemDutyConfig(models.Model):
    """Per-product switch whether the product triggers the parent duty."""

    item = models.OneToOneField(
        "pretixbase.Item", on_delete=models.CASCADE, related_name="parentduty_config",
    )
    triggers_duty = models.BooleanField(
        default=False, verbose_name=_("This product triggers the parent duty"),
        help_text=_("If checked, buyers of this product are asked to commit to a parent duty during checkout."),
    )


class DutyWaitlistEntry(models.Model):
    """A family wants another (full) slot instead of its current duty.

    The family always keeps its current duty; the waitlist only expresses a preference.
    """

    STATUS_WAITING = "waiting"
    STATUS_FULFILLED = "fulfilled"
    STATUS_REMOVED = "removed"
    STATUS_CHOICES = (
        (STATUS_WAITING, _("Waiting")),
        (STATUS_FULFILLED, _("Fulfilled")),
        (STATUS_REMOVED, _("Removed")),
    )

    event = models.ForeignKey(
        "pretixbase.Event", on_delete=models.CASCADE, related_name="parentduty_waitlist",
    )
    slot = models.ForeignKey(DutySlot, on_delete=models.CASCADE, related_name="waitlist_entries")
    signup = models.ForeignKey(DutySignup, on_delete=models.CASCADE, related_name="waitlist_entries")
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default=STATUS_WAITING)
    created = models.DateTimeField(auto_now_add=True)
    notified_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ("created", "id")
        constraints = [
            models.UniqueConstraint(fields=["slot", "signup"], name="ppd_waitlist_unique_slot_signup"),
        ]
        indexes = [models.Index(fields=["slot", "status"])]

    def __str__(self):
        return "{} → {}".format(self.signup.order.code, self.slot)
