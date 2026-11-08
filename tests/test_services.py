import pytest

from pretix_parentduties.models import DutySignup, ItemDutyConfig
from pretix_parentduties.services import (
    SlotFull, check_slot_choice, create_signups_for_order, family_code,
    find_family_signup, find_signup_by_code, normalize_email, positions_trigger,
    triggering_item_ids,
)

from .conftest import make_order


def slot_choice(slot, **kw):
    d = {"choice": "slot", "slots": [slot.pk], "helper_name": "Anna", "helper_phone": "0151"}
    d.update(kw)
    return d


def test_normalize_email():
    assert normalize_email("  Anna.Muster@Example.ORG ") == "anna.muster@example.org"
    assert normalize_email("a b@x.de") == "ab@x.de"
    assert normalize_email(None) == ""


def test_item_switch_and_default(event, item, donation):
    assert triggering_item_ids(event) == {item.pk}
    event.settings.parentduties_new_items_trigger = True
    assert triggering_item_ids(event) == {item.pk, donation.pk}
    ItemDutyConfig.objects.create(item=donation, triggers_duty=False)
    assert triggering_item_ids(event) == {item.pk}


def test_mixed_cart_triggers(event, item, donation):
    donation_order = make_order(event, donation)
    assert not positions_trigger(event, donation_order.positions.all())
    from pretix.base.models import OrderPosition
    OrderPosition.objects.create(order=donation_order, item=item, price=0)
    assert positions_trigger(event, donation_order.positions.all())


def test_slot_signup(event, item, slot):
    order = make_order(event, item)
    [s] = create_signups_for_order(order, slot_choice(slot))
    assert s.kind == DutySignup.KIND_SLOT
    assert s.status == DutySignup.STATUS_ACTIVE
    assert s.helper_name == "Anna"
    assert s.email_normalized == "familie@example.org"
    assert slot.occupied_count() == 1
    assert family_code(s) == order.code


def test_not_live_creates_nothing(event, item, slot):
    event.settings.parentduties_live = False
    order = make_order(event, item)
    assert create_signups_for_order(order, slot_choice(slot)) == []


def test_donation_only_creates_nothing(event, donation, slot):
    order = make_order(event, donation)
    assert create_signups_for_order(order, slot_choice(slot)) == []


def test_idempotent(event, item, slot):
    order = make_order(event, item)
    create_signups_for_order(order, slot_choice(slot))
    assert create_signups_for_order(order, slot_choice(slot)) == []
    assert order.parentduty_signups.count() == 1


def test_overbooked_goes_to_review(event, item, slot):
    slot.capacity = 1
    slot.save()
    create_signups_for_order(make_order(event, item, email="a@x.de"), slot_choice(slot))
    order = make_order(event, item, email="b@x.de")
    [s] = create_signups_for_order(order, slot_choice(slot))
    assert s.status == DutySignup.STATUS_NEEDS_REVIEW
    assert order.all_logentries().filter(action_type="pretix_parentduties.signup.overbooked").exists()


def test_check_slot_choice(event, item, slot):
    slot.capacity = 1
    slot.save()
    check_slot_choice(event, [slot.pk])
    create_signups_for_order(make_order(event, item), slot_choice(slot))
    with pytest.raises(SlotFull):
        check_slot_choice(event, [slot.pk])
    with pytest.raises(SlotFull):
        check_slot_choice(event, [999999])


def test_family_by_email(event, item, slot):
    first = make_order(event, item, email="Familie@Example.org")
    create_signups_for_order(first, slot_choice(slot))
    assert find_family_signup(event, " familie@example.ORG ")

    second = make_order(event, item, email="familie@example.org")
    [s] = create_signups_for_order(second, {"choice": "family"})
    assert s.kind == DutySignup.KIND_SIBLING
    assert s.status == DutySignup.STATUS_ACTIVE
    assert s.covered_by.order == first
    assert family_code(s) == first.code


def test_family_is_per_event(event, item, slot, organizer):
    from pretix.base.models import Event, Item
    create_signups_for_order(make_order(event, item), slot_choice(slot))
    other = Event.objects.create(organizer=organizer, name="Zirkus", slug="zirkus",
                                 date_from=event.date_from, plugins="pretix_parentduties")
    assert find_family_signup(other, "familie@example.org") is None
    other.settings.parentduties_live = True
    it = Item.objects.create(event=other, name="Kind", default_price=0)
    ItemDutyConfig.objects.create(item=it, triggers_duty=True)
    [s] = create_signups_for_order(make_order(other, it), None)
    assert s.status == DutySignup.STATUS_NEEDS_REVIEW


def test_canceled_orders_do_not_count(event, item, slot):
    from pretix.base.models import Order
    first = make_order(event, item)
    create_signups_for_order(first, slot_choice(slot))
    first.status = Order.STATUS_CANCELED
    first.save()
    assert find_family_signup(event, "familie@example.org") is None


def test_sibling_code_and_chain(event, item, slot):
    root_order = make_order(event, item, email="mama@x.de")
    create_signups_for_order(root_order, slot_choice(slot))
    second = make_order(event, item, email="papa@x.de")
    [s2] = create_signups_for_order(second, {"choice": "sibling", "sibling_code": root_order.code.lower()})
    assert s2.status == DutySignup.STATUS_ACTIVE
    assert s2.covered_by.order == root_order

    third = make_order(event, item, email="oma@x.de")
    [s3] = create_signups_for_order(third, {"choice": "sibling", "sibling_code": second.code})
    assert s3.status == DutySignup.STATUS_ACTIVE
    assert s3.covered_by.order == root_order  # chain followed to the root
    assert find_signup_by_code(event, second.code).order == root_order


def test_unknown_sibling_code_needs_review(event, item):
    order = make_order(event, item)
    [s] = create_signups_for_order(order, {"choice": "sibling", "sibling_code": "XXXXX"})
    assert s.kind == DutySignup.KIND_SIBLING
    assert s.status == DutySignup.STATUS_NEEDS_REVIEW
    assert s.sibling_code == "XXXXX"


def test_cannot_help(event, item):
    order = make_order(event, item)
    [s] = create_signups_for_order(order, {"choice": "cannot_help", "note": "Schichtdienst"})
    assert s.kind == DutySignup.KIND_CANNOT_HELP
    assert s.note == "Schichtdienst"
    # cannot_help does not cover siblings
    assert find_family_signup(event, "familie@example.org") is None


def test_skip_creates_nothing(event, item):
    assert create_signups_for_order(make_order(event, item), {"choice": "skip"}) == []


def test_backend_order_without_choice(event, item):
    [s] = create_signups_for_order(make_order(event, item), None)
    assert s.kind == DutySignup.KIND_SLOT and s.slot is None
    assert s.status == DutySignup.STATUS_NEEDS_REVIEW


def test_multiple_duties(event, item, slot, slot2):
    event.settings.parentduties_allow_multiple = True
    event.settings.parentduties_max_per_family = 2
    order = make_order(event, item)
    signups = create_signups_for_order(order, {"choice": "slot", "slots": [slot.pk, slot2.pk, 12345]})
    assert len(signups) == 2
    assert signups[0].primary and not signups[1].primary
