from django.utils import translation

from pretix.base.services.placeholders import get_available_placeholders

from pretix_parentduties.placeholders import ORDER_VALUES, order_value, register_layout_variables
from pretix_parentduties.services import create_signups_for_order

from .conftest import make_order

ALL_ORDER = list(ORDER_VALUES) + ["parent_duty_all"]


def test_registered_for_event(event):
    ph = get_available_placeholders(event, ["event", "order"])
    for name in ALL_ORDER + ["parent_duty_free_spots", "parent_duty_open_slots"]:
        assert name in ph, name
        assert isinstance(ph[name].render_sample(event), str)


def test_not_registered_without_plugin(event):
    event.disable_plugin("pretix_parentduties")
    event.save()
    ph = get_available_placeholders(event, ["event", "order"])
    assert "parent_duty" not in ph


def test_values_for_slot(event, item, slot):
    order = make_order(event, item)
    create_signups_for_order(order, {"choice": "slot", "slots": [slot.pk], "helper_name": "Anna"})
    with translation.override("en"):
        assert order_value("parent_duty_name", order) == "Kuchen Montag"
        assert order_value("parent_duty_category", order) == "Kuchen"
        assert order_value("parent_duty_time_from", order) == "14:00"
        assert order_value("parent_duty_time_to", order) == "16:00"
        assert order_value("parent_duty_helper_name", order) == "Anna"
        assert order_value("parent_duty_family_code", order) == order.code
        assert order_value("parent_duty_status", order) == "committed"
        assert "Kuchen Montag" in order_value("parent_duty", order)
    with translation.override("de"):
        assert order_value("parent_duty_time", order) == "14:00–16:00 Uhr"


def test_sibling_values(event, item, slot):
    first = make_order(event, item)
    create_signups_for_order(first, {"choice": "slot", "slots": [slot.pk]})
    second = make_order(event, item)
    create_signups_for_order(second, None)
    with translation.override("en"):
        assert first.code in order_value("parent_duty", second)
        assert order_value("parent_duty_family_code", second) == first.code
        assert order_value("parent_duty_name", second) == "Kuchen Montag"


def test_empty_without_signup(event, item):
    event.settings.parentduties_live = False
    order = make_order(event, item)
    for name in ALL_ORDER:
        assert order_value(name, order) == "", name


def test_event_placeholders(event, item, slot, slot2):
    create_signups_for_order(make_order(event, item), {"choice": "slot", "slots": [slot.pk]})
    ph = get_available_placeholders(event, ["event"])
    assert ph["parent_duty_free_spots"].render({"event": event}) == "6"
    assert "Aufbau Freitag" in ph["parent_duty_open_slots"].render({"event": event})


def test_layout_variables(event, item, slot):
    order = make_order(event, item)
    create_signups_for_order(order, {"choice": "slot", "slots": [slot.pk]})
    variables = register_layout_variables(sender=event)
    v = variables["parentduties_parent_duty_name"]
    assert v["evaluate"](order.positions.first(), order, event) == "Kuchen Montag"
