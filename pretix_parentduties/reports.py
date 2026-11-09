"""Fields for pretix-reportgenerator (``pretix_custom_reports``).

Only imported if the report generator is installed (see ``apps.py``). All values are
annotated as ``Subquery`` on the primary signup of the order – never per row.
"""
from django.db.models import Count, OuterRef, Q, Subquery
from django.db.models.functions import Coalesce
from django.dispatch import receiver
from django.utils.translation import gettext_lazy as _

from pretix_custom_reports.contracts import (
    Base, DataType, FieldContext, Operator, ReportField, plugin_field_key,
)
from pretix_custom_reports.registry.signals import register_report_fields

from .models import DutySignup

APP_LABEL = "pretix_parentduties"
GROUP = _("Parent duties")
PREFIX = "ppd_"


def _order_ref(ctx: FieldContext):
    return OuterRef("pk" if ctx.base is Base.ORDER else "order_id")


def _primary(ctx: FieldContext):
    return DutySignup.objects.filter(
        event=ctx.event, order_id=_order_ref(ctx), primary=True,
    ).order_by("id")


def _value(path):
    def annotation(ctx: FieldContext):
        return {PREFIX + path.replace("__", "_"): Subquery(_primary(ctx).values(path)[:1])}
    return annotation


def _family_code(ctx: FieldContext):
    # Own code for slot/exempt, code of the covering order for siblings (one level,
    # chains are resolved to the root on creation).
    qs = _primary(ctx)
    return {
        PREFIX + "family_code": Coalesce(
            Subquery(qs.filter(kind=DutySignup.KIND_SIBLING).values("covered_by__order__code")[:1]),
            Subquery(qs.filter(kind__in=(DutySignup.KIND_SLOT, DutySignup.KIND_EXEMPT)).values("order__code")[:1]),
        )
    }


def _duty_count(ctx: FieldContext):
    counts = DutySignup.objects.filter(
        event=ctx.event, order_id=_order_ref(ctx),
    ).exclude(status=DutySignup.STATUS_CANCELED).order_by().values("order_id").annotate(
        c=Count("id", filter=Q(kind=DutySignup.KIND_SLOT))
    ).values("c")[:1]
    return {PREFIX + "duty_count": Coalesce(Subquery(counts), 0)}


def _choices(choices):
    return lambda ctx: [(k, str(v)) for k, v in choices]


FIELDS = [
    # name, label, datatype, orm source, operators, sortable, extra kwargs
    ("duty", _("Duty"), DataType.I18N, "slot__name", (Operator.CONTAINS, Operator.EXACT), True, {}),
    ("category", _("Duty category"), DataType.STRING, "slot__tag", (Operator.EXACT,), True, {}),
    ("date", _("Duty date"), DataType.DATE, "slot__date", (Operator.BETWEEN, Operator.EXACT), True, {}),
    ("time_from", _("Duty start time"), DataType.TIME, "slot__time_from", (), True, {}),
    ("time_to", _("Duty end time"), DataType.TIME, "slot__time_to", (), True, {}),
    ("kind", _("Duty type"), DataType.CHOICE, "kind", (Operator.EXACT, Operator.IN), True,
     {"choices": _choices(DutySignup.KIND_CHOICES)}),
    ("status", _("Duty status"), DataType.CHOICE, "status", (Operator.EXACT, Operator.IN), True,
     {"choices": _choices(DutySignup.STATUS_CHOICES)}),
    ("helper_name", _("Helper name"), DataType.STRING, "helper_name", (Operator.CONTAINS,), True, {}),
    ("helper_phone", _("Helper phone"), DataType.PHONE, "helper_phone", (), False, {}),
    ("note", _("Duty note"), DataType.TEXT, "note", (Operator.CONTAINS,), False, {}),
]


def build_fields(base):
    result = []
    for name, label, datatype, source, ops, sortable, extra in FIELDS:
        result.append(ReportField(
            key=plugin_field_key(APP_LABEL, name),
            label=label,
            group=GROUP,
            datatype=datatype,
            bases=(base,),
            orm_path=PREFIX + source.replace("__", "_"),
            annotation=_value(source),
            filter_operators=ops,
            sortable=sortable,
            provider=APP_LABEL,
            **extra,
        ))
    result.append(ReportField(
        key=plugin_field_key(APP_LABEL, "family_code"), label=_("Family order code"), group=GROUP,
        datatype=DataType.STRING, bases=(base,), orm_path=PREFIX + "family_code", annotation=_family_code,
        filter_operators=(Operator.EXACT,), sortable=True, provider=APP_LABEL,
    ))
    result.append(ReportField(
        key=plugin_field_key(APP_LABEL, "duty_count"), label=_("Number of duties"), group=GROUP,
        datatype=DataType.INTEGER, bases=(base,), orm_path=PREFIX + "duty_count", annotation=_duty_count,
        filter_operators=(Operator.BETWEEN, Operator.GTE, Operator.LTE, Operator.EXACT), sortable=True,
        provider=APP_LABEL,
    ))
    return result


@receiver(register_report_fields, dispatch_uid="pretix_parentduties_report_fields")
def report_fields(sender, base, **kwargs):
    b = Base.coerce(base)
    if b not in (Base.ORDER, Base.ORDERPOSITION):
        return []
    return build_fields(b)
