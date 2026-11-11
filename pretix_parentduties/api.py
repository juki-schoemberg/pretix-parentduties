"""REST API: duty slots, signups and waitlist of an event.

Endpoints (below ``/api/v1/organizers/<organizer>/events/<event>/``):

* ``parentduty_categories/`` – CRUD for categories (their ``position`` defines the order)
* ``parentduty_slots/`` – CRUD for duty slots, with occupancy (``occupied``, ``free``, ``waiting``)
* ``parentduty_signups/`` – read signups, filterable; ``POST …/<id>/move/`` moves one to another slot
* ``parentduty_waitlist/`` – read waitlist entries, filterable

Permissions: reading needs "Parent duties: view", writing "Parent duties: view and change".
"""
from django.db import transaction
from django_filters.rest_framework import CharFilter, DjangoFilterBackend, FilterSet
from django_scopes import scopes_disabled
from rest_framework import serializers, status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import NotFound, ValidationError
from rest_framework.response import Response

from pretix.api.serializers.i18n import I18nAwareModelSerializer

from . import services
from .models import DutyCategory, DutySignup, DutySlot, DutyWaitlistEntry

PLUGIN = "pretix_parentduties"
PERM_READ = "event.parentduties:read"
PERM_WRITE = "event.parentduties:write"


class PluginActiveMixin:
    def initial(self, request, *args, **kwargs):
        super().initial(request, *args, **kwargs)
        if PLUGIN not in request.event.get_plugins():
            raise NotFound("The parent duties plugin is not active for this event.")


# ------------------------------------------------------------- categories

class DutyCategorySerializer(I18nAwareModelSerializer):
    class Meta:
        model = DutyCategory
        fields = ("id", "name", "description", "position")
        read_only_fields = ("id",)


class DutyCategoryViewSet(PluginActiveMixin, viewsets.ModelViewSet):
    serializer_class = DutyCategorySerializer
    queryset = DutyCategory.objects.none()
    permission = PERM_READ
    write_permission = PERM_WRITE

    def get_queryset(self):
        return DutyCategory.objects.filter(event=self.request.event).order_by("position", "id")

    @transaction.atomic
    def perform_create(self, serializer):
        serializer.save(event=self.request.event)

    @transaction.atomic
    def perform_destroy(self, instance):
        instance.delete()  # slots of the category become uncategorized


# ------------------------------------------------------------------ slots

class DutySlotSerializer(I18nAwareModelSerializer):
    occupied = serializers.SerializerMethodField()
    free = serializers.SerializerMethodField()
    waiting = serializers.SerializerMethodField()

    class Meta:
        model = DutySlot
        fields = (
            "id", "name", "description", "category", "subevent", "date", "time_from", "time_to",
            "capacity", "active", "position", "limit_items", "occupied", "free", "waiting",
        )
        read_only_fields = ("id", "occupied", "free", "waiting")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        event = self.context["request"].event if "request" in self.context else None
        if event is not None:
            self.fields["limit_items"].child_relation.queryset = event.items.all()
            self.fields["category"].queryset = DutyCategory.objects.filter(event=event)
            self.fields["subevent"].queryset = event.subevents.all()

    def get_occupied(self, obj):
        return obj.occupied_count()

    def get_free(self, obj):
        return obj.free_count()

    def get_waiting(self, obj):
        if hasattr(obj, "waiting"):
            return obj.waiting
        return obj.waitlist_entries.filter(status=DutyWaitlistEntry.STATUS_WAITING).count()

    def validate(self, data):
        data = super().validate(data)
        tf = data.get("time_from", getattr(self.instance, "time_from", None))
        tt = data.get("time_to", getattr(self.instance, "time_to", None))
        if tf and tt and tt < tf:
            raise ValidationError({"time_to": ["The end time must not be before the start time."]})
        return data


with scopes_disabled():
    class DutySlotFilter(FilterSet):
        class Meta:
            model = DutySlot
            fields = ["active", "category", "date", "subevent"]


class DutySlotViewSet(PluginActiveMixin, viewsets.ModelViewSet):
    serializer_class = DutySlotSerializer
    queryset = DutySlot.objects.none()
    filter_backends = (DjangoFilterBackend,)
    filterset_class = DutySlotFilter
    permission = PERM_READ
    write_permission = PERM_WRITE

    def get_queryset(self):
        return DutySlot.objects.filter(event=self.request.event).with_occupancy().with_waiting() \
            .select_related("category").prefetch_related("limit_items") \
            .order_by("category__position", "category_id", "position", "id")

    def _log(self, action_type, slot):
        self.request.event.log_action(
            action_type, user=self.request.user, auth=self.request.auth,
            data={"id": slot.pk, "name": str(slot.name)},
        )

    @transaction.atomic
    def perform_create(self, serializer):
        slot = serializer.save(event=self.request.event)
        self._log("pretix_parentduties.slot.created", slot)

    @transaction.atomic
    def perform_update(self, serializer):
        slot = serializer.save()
        self._log("pretix_parentduties.slot.changed", slot)
        transaction.on_commit(lambda: services.process_waitlist(slot))

    @transaction.atomic
    def perform_destroy(self, instance):
        if instance.signups.exists():
            raise ValidationError("This duty slot already has signups. Deactivate it instead (active=false).")
        self._log("pretix_parentduties.slot.deleted", instance)
        instance.delete()


# ---------------------------------------------------------------- signups

class DutySignupSerializer(serializers.ModelSerializer):
    order = serializers.SlugRelatedField(slug_field="code", read_only=True)
    covered_by_order = serializers.SerializerMethodField()
    family_code = serializers.SerializerMethodField()
    voucher = serializers.SlugRelatedField(slug_field="code", read_only=True)

    class Meta:
        model = DutySignup
        fields = (
            "id", "order", "primary", "kind", "status", "slot", "covered_by", "covered_by_order",
            "family_code", "sibling_code", "voucher", "helper_name", "helper_phone", "note", "created",
        )
        read_only_fields = fields

    def get_covered_by_order(self, obj):
        return obj.covered_by.order.code if obj.covered_by_id else None

    def get_family_code(self, obj):
        return services.family_code(obj) or None


with scopes_disabled():
    class DutySignupFilter(FilterSet):
        order = CharFilter(field_name="order__code", lookup_expr="iexact")

        class Meta:
            model = DutySignup
            fields = ["status", "kind", "slot", "primary", "order"]


class MoveSerializer(serializers.Serializer):
    slot = serializers.IntegerField()


class DutySignupViewSet(PluginActiveMixin, viewsets.ReadOnlyModelViewSet):
    serializer_class = DutySignupSerializer
    queryset = DutySignup.objects.none()
    filter_backends = (DjangoFilterBackend,)
    filterset_class = DutySignupFilter
    permission = PERM_READ
    write_permission = PERM_WRITE

    def get_queryset(self):
        return DutySignup.objects.filter(event=self.request.event).select_related(
            "order", "slot", "covered_by__order", "voucher",
        ).order_by("id")

    @action(detail=True, methods=["post"])
    @transaction.atomic
    def move(self, request, *args, **kwargs):
        """Move a signup to another slot (like the backend; capacity may be exceeded deliberately)."""
        signup = self.get_object()
        ser = MoveSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        slot = DutySlot.objects.filter(event=request.event, pk=ser.validated_data["slot"]).first()
        if slot is None:
            raise ValidationError({"slot": ["Unknown duty slot."]})
        if signup.status == DutySignup.STATUS_CANCELED:
            raise ValidationError("Canceled signups cannot be moved.")
        services.admin_move(signup, slot, user=request.user if request.user.is_authenticated else None)
        signup.refresh_from_db()
        return Response(DutySignupSerializer(signup, context={"request": request}).data, status=status.HTTP_200_OK)


# --------------------------------------------------------------- waitlist

class DutyWaitlistEntrySerializer(serializers.ModelSerializer):
    order = serializers.CharField(source="signup.order.code", read_only=True)

    class Meta:
        model = DutyWaitlistEntry
        fields = ("id", "slot", "signup", "order", "status", "created", "notified_at")
        read_only_fields = fields


with scopes_disabled():
    class DutyWaitlistFilter(FilterSet):
        class Meta:
            model = DutyWaitlistEntry
            fields = ["slot", "status", "signup"]


class DutyWaitlistViewSet(PluginActiveMixin, viewsets.ReadOnlyModelViewSet):
    serializer_class = DutyWaitlistEntrySerializer
    queryset = DutyWaitlistEntry.objects.none()
    filter_backends = (DjangoFilterBackend,)
    filterset_class = DutyWaitlistFilter
    permission = PERM_READ

    def get_queryset(self):
        return DutyWaitlistEntry.objects.filter(event=self.request.event).select_related(
            "signup__order",
        ).order_by("created", "id")
