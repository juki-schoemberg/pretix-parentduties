"""REST API for slots, signups and the waitlist."""
import pytest
from rest_framework.test import APIClient

from pretix.base.models import Team

from pretix_parentduties import services
from pretix_parentduties.models import DutySignup, DutySlot, DutyWaitlistEntry
from pretix_parentduties.services import create_signups_for_order

from .conftest import make_order


def api_client(event, permissions):
    team = Team.objects.create(organizer=event.organizer, all_events=True, limit_event_permissions=permissions)
    token = team.tokens.create(name="test")
    c = APIClient()
    c.credentials(HTTP_AUTHORIZATION="Token " + token.token)
    return c


@pytest.fixture
def reader(event):
    return api_client(event, {"event.parentduties:read": True})


@pytest.fixture
def writer(event):
    return api_client(event, {"event.parentduties:read": True, "event.parentduties:write": True})


def url(event, path):
    return f"/api/v1/organizers/{event.organizer.slug}/events/{event.slug}/{path}"


def test_list_slots_with_occupancy(event, item, slot, reader):
    create_signups_for_order(make_order(event, item), {"choice": "slot", "slots": [slot.pk]})
    r = reader.get(url(event, "parentduty_slots/"))
    assert r.status_code == 200, r.content
    [s] = r.data["results"]
    assert s["name"] == {"en": "Kuchen Montag"}
    assert (s["capacity"], s["occupied"], s["free"], s["waiting"]) == (2, 1, 1, 0)


def test_no_permission(event, slot):
    c = api_client(event, {"event.orders:read": True})
    assert c.get(url(event, "parentduty_slots/")).status_code == 403


def test_reader_cannot_write(event, reader):
    r = reader.post(url(event, "parentduty_slots/"), {"name": {"de": "Aufbau"}}, format="json")
    assert r.status_code == 403


def test_create_update_delete_slot(event, item, writer):
    r = writer.post(url(event, "parentduty_slots/"), {
        "name": {"de": "Aufbau Freitag"}, "tag": "Aufbau", "capacity": 4, "limit_items": [item.pk],
        "date": "2027-07-09", "time_from": "16:00", "time_to": "18:00",
    }, format="json")
    assert r.status_code == 201, r.content
    slot = DutySlot.objects.get(pk=r.data["id"])
    assert list(slot.limit_items.all()) == [item] and slot.event == event
    r = writer.patch(url(event, f"parentduty_slots/{slot.pk}/"), {"capacity": 6}, format="json")
    assert r.status_code == 200 and r.data["capacity"] == 6
    r = writer.patch(url(event, f"parentduty_slots/{slot.pk}/"), {"time_to": "15:00"}, format="json")
    assert r.status_code == 400
    assert writer.delete(url(event, f"parentduty_slots/{slot.pk}/")).status_code == 204
    assert not DutySlot.objects.filter(pk=slot.pk).exists()


def test_delete_slot_with_signups_refused(event, item, slot, writer):
    create_signups_for_order(make_order(event, item), {"choice": "slot", "slots": [slot.pk]})
    assert writer.delete(url(event, f"parentduty_slots/{slot.pk}/")).status_code == 400


def test_foreign_items_rejected(event, organizer, writer):
    from pretix.base.models import Event, Item
    other = Event.objects.create(organizer=organizer, name="X", slug="x", date_from=event.date_from)
    foreign = Item.objects.create(event=other, name="Fremd", default_price=0)
    r = writer.post(url(event, "parentduty_slots/"), {"name": {"de": "A"}, "limit_items": [foreign.pk]},
                    format="json")
    assert r.status_code == 400


def test_signups_filter_and_move(event, item, slot, slot2, reader, writer):
    o1 = make_order(event, item, email="a@x.de")
    create_signups_for_order(o1, {"choice": "slot", "slots": [slot.pk], "helper_name": "Anna"})
    o2 = make_order(event, item, email="b@x.de")
    create_signups_for_order(o2, None)
    r = reader.get(url(event, "parentduty_signups/?status=needs_review"))
    assert [s["order"] for s in r.data["results"]] == [o2.code]
    r = reader.get(url(event, f"parentduty_signups/?order={o1.code.lower()}"))
    [s] = r.data["results"]
    assert s["helper_name"] == "Anna" and s["family_code"] == o1.code and s["slot"] == slot.pk

    signup = o2.parentduty_signups.get()
    assert reader.post(url(event, f"parentduty_signups/{signup.pk}/move/"), {"slot": slot2.pk}).status_code == 403
    r = writer.post(url(event, f"parentduty_signups/{signup.pk}/move/"), {"slot": slot2.pk}, format="json")
    assert r.status_code == 200, r.content
    assert r.data["slot"] == slot2.pk and r.data["status"] == "active"
    signup.refresh_from_db()
    assert signup.slot == slot2 and signup.status == DutySignup.STATUS_ACTIVE
    assert writer.post(url(event, f"parentduty_signups/{signup.pk}/move/"), {"slot": 99999},
                       format="json").status_code == 400


def test_waitlist_endpoint(event, item, slot, slot2, reader):
    event.settings.parentduties_waitlist_mode = "auto"
    slot.capacity = 1
    slot.save()
    create_signups_for_order(make_order(event, item, email="a@x.de"), {"choice": "slot", "slots": [slot.pk]})
    o = make_order(event, item, email="b@x.de")
    create_signups_for_order(o, {"choice": "slot", "slots": [slot2.pk]})
    services.join_waitlist(o.parentduty_signups.get(), slot)
    r = reader.get(url(event, "parentduty_waitlist/?status=waiting"))
    [e] = r.data["results"]
    assert e["order"] == o.code and e["slot"] == slot.pk
    assert DutyWaitlistEntry.objects.count() == 1


def test_plugin_disabled(event, slot, reader):
    event.disable_plugin("pretix_parentduties")
    event.save()
    assert reader.get(url(event, "parentduty_slots/")).status_code == 404
