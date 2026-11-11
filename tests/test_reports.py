"""Report fields for pretix-reportgenerator; skipped if it is not installed."""
import json
from pathlib import Path

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
    assert "Kuchen" in str(rows[first.code].ppd_slot_category_name)
    assert rows[first.code].ppd_family_code == first.code
    assert rows[second.code].ppd_family_code == first.code
    assert rows[first.code].ppd_duty_count == 1


TEMPLATES = Path(__file__).resolve().parent.parent / "docs" / "reports"


def _compile(event, filename):
    from pretix_custom_reports.contracts import validate_portable_document
    from pretix_custom_reports.query.compiler import compile_report
    from pretix_custom_reports.registry.library import field_registry
    doc = validate_portable_document(json.loads((TEMPLATES / filename).read_text(encoding="utf-8")))
    return compile_report(doc.definition, event, field_registry())


def _codes(compiled):
    idx = [c.key for c in compiled.columns].index("order.code")
    return [row[idx] for row in compiled.iter_rows()]


def test_template_helper_list(event, item, slot, slot2):
    import datetime
    slot2.date = datetime.date(2027, 7, 16)  # NULL ordering differs between databases
    slot2.save()
    a = make_order(event, item, email="a@x.de")
    create_signups_for_order(a, {"choice": "slot", "slots": [slot2.pk], "helper_name": "Anna"})
    b = make_order(event, item, email="b@x.de")
    create_signups_for_order(b, {"choice": "slot", "slots": [slot.pk], "helper_name": "Ben"})
    review = make_order(event, item, email="c@x.de")
    create_signups_for_order(review, None)
    compiled = _compile(event, "helferliste-je-tag.json")
    # sorted by date; open duties are not on the helper list
    assert _codes(compiled) == [b.code, a.code]


def test_template_review_list(event, item, slot):
    ok = make_order(event, item, email="a@x.de")
    create_signups_for_order(ok, {"choice": "slot", "slots": [slot.pk]})
    review = make_order(event, item, email="b@x.de")
    create_signups_for_order(review, None)
    cannot = make_order(event, item, email="c@x.de")
    create_signups_for_order(cannot, {"choice": "cannot_help", "note": "Schicht"})
    compiled = _compile(event, "familien-in-klaerung.json")
    assert sorted(_codes(compiled)) == sorted([review.code, cannot.code])
