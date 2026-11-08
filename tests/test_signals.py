import json

from pretix.base.models import Event, Item, Order, OrderPosition

from pretix_parentduties.models import DutySignup, DutySlot, ItemDutyConfig
from pretix_parentduties.services import create_signups_for_order
from pretix_parentduties.signals import (
    ParentDutyShredder, copy_event_data, copy_item_config, on_order_canceled,
    on_order_changed, on_order_expired, on_order_gracefully_delete, on_order_placed,
    on_order_reactivated, on_order_split,
)

from .conftest import make_order


def place(event, item, slot, **kw):
    order = make_order(event, item, **kw)
    order.meta_info = json.dumps({"pretix_parentduties": {
        "choice": "slot", "slots": [slot.pk], "helper_name": "Anna", "helper_phone": "0151",
    }})
    order.save()
    on_order_placed(sender=event, order=order)
    return order


def test_order_placed_reads_meta_info(event, item, slot):
    order = place(event, item, slot)
    s = order.parentduty_signups.get()
    assert s.slot == slot and s.status == DutySignup.STATUS_ACTIVE


def test_cancel_frees_spot_and_flags_siblings(event, item, slot):
    first = place(event, item, slot)
    second = make_order(event, item)
    on_order_placed(sender=event, order=second)
    sib = second.parentduty_signups.get()
    assert sib.kind == DutySignup.KIND_SIBLING and sib.status == DutySignup.STATUS_ACTIVE

    first.status = Order.STATUS_CANCELED
    first.save()
    on_order_canceled(sender=event, order=first)
    assert first.parentduty_signups.get().status == DutySignup.STATUS_CANCELED
    assert slot.occupied_count() == 0
    sib.refresh_from_db()
    assert sib.status == DutySignup.STATUS_NEEDS_REVIEW


def test_expire(event, item, slot):
    order = place(event, item, slot)
    on_order_expired(sender=event, order=order)
    assert order.parentduty_signups.get().status == DutySignup.STATUS_CANCELED


def test_reactivate_with_and_without_space(event, item, slot):
    slot.capacity = 1
    slot.save()
    order = place(event, item, slot, email="a@x.de")
    on_order_canceled(sender=event, order=order)
    on_order_reactivated(sender=event, order=order)
    assert order.parentduty_signups.get().status == DutySignup.STATUS_ACTIVE

    on_order_canceled(sender=event, order=order)
    place(event, item, slot, email="b@x.de")  # takes the free spot
    on_order_reactivated(sender=event, order=order)
    assert order.parentduty_signups.get().status == DutySignup.STATUS_NEEDS_REVIEW


def test_split(event, item, slot):
    original = place(event, item, slot)
    split = make_order(event, item)
    on_order_split(sender=event, original=original, split_order=split)
    s = split.parentduty_signups.get()
    assert s.kind == DutySignup.KIND_SIBLING
    assert s.covered_by == original.parentduty_signups.get()


def test_changed_without_triggering_product(event, item, donation, slot):
    order = place(event, item, slot)
    on_order_changed(sender=event, order=order)
    assert order.parentduty_signups.get().status == DutySignup.STATUS_ACTIVE
    OrderPosition.objects.filter(order=order).update(item=donation)
    on_order_changed(sender=event, order=order)
    s = order.parentduty_signups.get()
    assert s.status == DutySignup.STATUS_NEEDS_REVIEW
    assert DutySignup.objects.filter(pk=s.pk).exists()


def test_gracefully_delete(event, item, slot):
    order = place(event, item, slot)
    on_order_gracefully_delete(sender=event, order=order)
    assert not DutySignup.objects.filter(order=order).exists()
    order.positions.all().delete()
    order.delete()


def test_event_copy(event, item, slot, organizer):
    slot.limit_items.add(item)
    place(event, item, slot)
    new = Event.objects.create(organizer=organizer, name="Schömsala 2028", slug="s28",
                               date_from=event.date_from, plugins="pretix_parentduties")
    new_item = Item.objects.create(event=new, name="Kind", default_price=0)
    copy_event_data(sender=new, other=event, item_map={item.pk: new_item})
    new_slot = DutySlot.objects.get(event=new)
    assert str(new_slot.name) == "Kuchen Montag"
    assert new_slot.date is None
    assert new_slot.capacity == 2
    assert list(new_slot.limit_items.all()) == [new_item]
    assert not new_slot.signups.exists()
    assert ItemDutyConfig.objects.get(item=new_item).triggers_duty


def test_item_copy(event, item):
    target = Item.objects.create(event=event, name="Kind 2", default_price=0)
    copy_item_config(sender=event, source=item, target=target)
    assert target.parentduty_config.triggers_duty


def test_shredder(event, item, slot):
    order = place(event, item, slot)
    s = order.parentduty_signups.get()
    s.note = "privat"
    s.save()
    shredder = ParentDutyShredder(event)
    files = list(shredder.generate_files())
    assert "Anna" in files[0][2]
    shredder.shred_data()
    s.refresh_from_db()
    assert (s.helper_name, s.helper_phone, s.note) == ("", "", "")
    order = Order.objects.get(pk=order.pk)  # meta_info_data is a cached_property
    assert "helper_name" not in order.meta_info_data["pretix_parentduties"]


def test_validate_order_blocks_full_slot(event, item, slot):
    import pytest

    from pretix.base.services.orders import OrderError

    from pretix_parentduties.signals import on_validate_order
    slot.capacity = 1
    slot.save()
    create_signups_for_order(make_order(event, item), {"choice": "slot", "slots": [slot.pk]})
    positions = list(make_order(event, item).positions.all())
    meta = {"pretix_parentduties": {"choice": "slot", "slots": [slot.pk], "committed": True}}
    with pytest.raises(OrderError):
        on_validate_order(sender=event, positions=positions, meta_info=meta)

    meta["pretix_parentduties"]["committed"] = False
    slot.capacity = 5
    slot.save()
    with pytest.raises(OrderError):
        on_validate_order(sender=event, positions=positions, meta_info=meta)
    event.settings.parentduties_confirm_checkbox = False
    on_validate_order(sender=event, positions=positions, meta_info=meta)


def test_dashboard_widget(event, item, slot, slot2):
    from pretix_parentduties.dashboard import stats, widget
    place(event, item, slot)
    st = stats(event)
    assert st["capacity"] == 7 and st["occupied"] == 1
    assert [s.pk for s in st["empty"]] == [slot2.pk]
    w = widget(event)[0]
    assert "1 of 7" in w["content"]
    assert widget(event, lazy=True)[0]["content"] is None
