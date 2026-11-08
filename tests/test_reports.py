"""Report fields for pretix-reportgenerator; skipped if it is not installed."""
import pytest

pytest.importorskip("pretix_custom_reports")

from pretix.base.models import Order  # noqa: E402

from pretix_parentduties.reports import build_fields, report_fields  # noqa: E402
from pretix_parentduties.services import create_signups_for_order  # noqa: E402

from .conftest import make_order  # noqa: E402


def test_field_keys(event):
    from pretix_custom_reports.contracts import Base
    keys = {f.key for f in report_fields(sender=event, base="order")}
    for name in ("duty", "category", "date", "time_from", "time_to", "kind", "status",
                 "helper_name", "helper_phone", "family_code", "note", "duty_count"):
        assert f"plugin.pretix_parentduties.{name}" in keys
    assert all(f.provider == "pretix_parentduties" for f in build_fields(Base.ORDERPOSITION))


def test_annotations(event, item, slot):
    from pretix_custom_reports.contracts import Base, FieldContext
    first = make_order(event, item)
    create_signups_for_order(first, {"choice": "slot", "slots": [slot.pk], "helper_name": "Anna"})
    second = make_order(event, item)
    create_signups_for_order(second, None)

    ctx = FieldContext(event=event, base=Base.ORDER)
    qs = Order.objects.filter(event=event)
    for f in build_fields(Base.ORDER):
        qs = qs.annotate(**f.annotation(ctx))
    rows = {o.code: o for o in qs}
    assert rows[first.code].ppd_helper_name == "Anna"
    assert rows[first.code].ppd_slot_tag == "Kuchen"
    assert rows[first.code].ppd_family_code == first.code
    assert rows[second.code].ppd_family_code == first.code
    assert rows[first.code].ppd_duty_count == 1
