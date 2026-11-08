"""Smoke tests for the backend pages."""
import time

import pytest
from django.test import Client

from pretix.base.models import Team, User

from pretix_parentduties.models import DutySignup, DutySlot, ItemDutyConfig
from pretix_parentduties.services import create_signups_for_order

from .conftest import make_order


@pytest.fixture
def admin_client(event):
    user = User.objects.create_user("admin@example.org", "admin")
    team = Team.objects.create(organizer=event.organizer, all_event_permissions=True, all_events=True)
    team.members.add(user)
    c = Client()
    c.login(email="admin@example.org", password="admin")
    session = c.session
    session["pretix_auth_login_time"] = time.time()
    session.save()
    return c


@pytest.fixture
def limited_client(event):
    """Can view orders, but has no parent duty permission."""
    user = User.objects.create_user("viewer@example.org", "viewer")
    team = Team.objects.create(organizer=event.organizer, all_events=True,
                               limit_event_permissions={"event.orders:read": True})
    team.members.add(user)
    c = Client()
    c.login(email="viewer@example.org", password="viewer")
    return c


def url(event, path=""):
    return f"/control/event/{event.organizer.slug}/{event.slug}/parentduties/{path}"


def test_pages_render(admin_client, event, item, slot):
    order = make_order(event, item)
    create_signups_for_order(order, {"choice": "slot", "slots": [slot.pk], "helper_name": "Anna"})
    assert "parentduties_change_until" in admin_client.get(url(event, "settings/")).content.decode()
    for path in ("", "slots/add", f"slots/{slot.pk}/", "signups/", "signups/?review=1", "items/", "settings/"):
        r = admin_client.get(url(event, path))
        assert r.status_code == 200, path
    r = admin_client.get(url(event))
    assert "Kuchen Montag" in r.content.decode() and "1/2" in r.content.decode()
    r = admin_client.get(url(event, "signups/"))
    assert "Anna" in r.content.decode() and order.code in r.content.decode()
    r = admin_client.get(f"/control/event/{event.organizer.slug}/{event.slug}/orders/{order.code}/")
    assert "Kuchen Montag" in r.content.decode()
    r = admin_client.get(f"/control/event/{event.organizer.slug}/{event.slug}/items/{item.pk}/")
    assert "parentduties-triggers_duty" in r.content.decode()
    r = admin_client.get(f"/control/event/{event.organizer.slug}/{event.slug}/widgets.json")
    assert r.status_code == 200


def test_permission(limited_client, event):
    assert limited_client.get(url(event)).status_code == 403


def test_create_duplicate_delete_slot(admin_client, event, item):
    r = admin_client.post(url(event, "slots/add"), {
        "name_0": "Kuchen Dienstag", "tag": "Kuchen", "capacity": "5", "position": "0", "active": "on",
        "limit_items": [str(item.pk)],
    })
    assert r.status_code == 302, r.content.decode()[:3000]
    slot = DutySlot.objects.get(event=event)
    assert list(slot.limit_items.all()) == [item]
    admin_client.post(url(event, f"slots/{slot.pk}/duplicate"))
    assert DutySlot.objects.filter(event=event).count() == 2
    copy = DutySlot.objects.exclude(pk=slot.pk).get()
    assert list(copy.limit_items.all()) == [item]
    admin_client.post(url(event, f"slots/{copy.pk}/delete"))
    assert DutySlot.objects.filter(event=event).count() == 1


def test_delete_slot_with_signups_deactivates(admin_client, event, item, slot):
    create_signups_for_order(make_order(event, item), {"choice": "slot", "slots": [slot.pk]})
    admin_client.post(url(event, f"slots/{slot.pk}/delete"))
    slot.refresh_from_db()
    assert not slot.active


def test_signup_actions(admin_client, event, item, slot, slot2):
    order = make_order(event, item)
    [s] = create_signups_for_order(order, None)
    assert s.status == DutySignup.STATUS_NEEDS_REVIEW
    admin_client.post(url(event, f"signups/{s.pk}/action"), {"action": "move", "slot": slot2.pk})
    s.refresh_from_db()
    assert s.slot == slot2 and s.status == DutySignup.STATUS_ACTIVE
    admin_client.post(url(event, f"signups/{s.pk}/action"), {"action": "exempt"})
    s.refresh_from_db()
    assert s.kind == DutySignup.KIND_EXEMPT
    admin_client.post(url(event, f"signups/{s.pk}/action"), {"action": "cancel"})
    s.refresh_from_db()
    assert s.status == DutySignup.STATUS_CANCELED
    assert order.all_logentries().filter(action_type__startswith="pretix_parentduties.").count() >= 4

    other = make_order(event, item, email="papa@x.de")
    [o] = create_signups_for_order(other, {"choice": "sibling", "sibling_code": "NOPE"})
    root = create_signups_for_order(make_order(event, item, email="mama@x.de"),
                                    {"choice": "slot", "slots": [slot.pk]})[0]
    admin_client.post(url(event, f"signups/{o.pk}/action"), {"action": "link", "code": root.order.code})
    o.refresh_from_db()
    assert o.covered_by == root and o.status == DutySignup.STATUS_ACTIVE

    r = admin_client.post(url(event, "signups/add"), {"order": other.code, "kind": "slot", "slot": slot.pk})
    assert r.status_code == 302
    assert other.parentduty_signups.count() == 2


def test_items_overview(admin_client, event, item, donation):
    admin_client.post(url(event, "items/"), {"items": [str(donation.pk)]})
    assert not ItemDutyConfig.objects.get(item=item).triggers_duty
    assert ItemDutyConfig.objects.get(item=donation).triggers_duty


def test_settings(admin_client, event):
    r = admin_client.post(url(event, "settings/"), {
        "parentduties_live": "on", "parentduties_title_0": "Elterndienst", "parentduties_intro_0": "",
        "parentduties_max_per_family": "3", "parentduties_ask_name": "optional", "parentduties_ask_phone": "off",
    })
    assert r.status_code == 302
    event.settings.flush()
    assert event.settings.parentduties_max_per_family == 3
    assert event.settings.parentduties_ask_phone == "off"
    assert not event.settings.parentduties_required
