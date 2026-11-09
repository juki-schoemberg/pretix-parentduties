"""Waitlist for full duties: notify or move automatically when a spot becomes free."""
import datetime

import pytest
from django.core import mail as djmail
from django.test import Client
from django.utils.timezone import now

from pretix.base.models import Order, Quota
from pretix.base.reldate import RelativeDateWrapper

from pretix_parentduties import services
from pretix_parentduties.models import DutySignup, DutyWaitlistEntry
from pretix_parentduties.placeholders import order_value
from pretix_parentduties.services import create_signups_for_order

from .conftest import make_order


@pytest.fixture
def full_slot(slot):
    slot.capacity = 1
    slot.save()
    return slot


def book(event, item, slot, email, waitlist=None):
    order = make_order(event, item, email=email)
    create_signups_for_order(order, {"choice": "slot", "slots": [slot.pk], "waitlist": waitlist or []})
    return order, order.parentduty_signups.get(primary=True)


def waiting(signup):
    return list(DutyWaitlistEntry.objects.filter(signup=signup, status="waiting").values_list("slot_id", flat=True))


def page(order):
    return f"/{order.event.organizer.slug}/{order.event.slug}/order/{order.code}/{order.secret}/parentduty/"


@pytest.fixture
def auto(event):
    event.settings.parentduties_waitlist_mode = "auto"


@pytest.fixture
def notify(event):
    event.settings.parentduties_waitlist_mode = "notify"
    event.settings.parentduties_change_until = RelativeDateWrapper(now() + datetime.timedelta(days=3))


def test_off_by_default(event, item, full_slot, slot2):
    assert not services.waitlist_active(event)
    a, sa = book(event, item, full_slot, "a@x.de")
    b, sb = book(event, item, slot2, "b@x.de", waitlist=[full_slot.pk])
    assert waiting(sb) == []  # checkout data is ignored while the waitlist is off
    services.cancel_order_signups(a)
    sb.refresh_from_db()
    assert sb.slot == slot2


def test_auto_moves_oldest_and_chains(event, item, full_slot, slot2, auto):
    slot2.capacity = 1
    slot2.save()
    a, sa = book(event, item, full_slot, "a@x.de")
    b, sb = book(event, item, slot2, "b@x.de", waitlist=[full_slot.pk])
    # c waits for slot2, which b frees when moved
    from pretix_parentduties.models import DutySlot
    third = DutySlot.objects.create(event=event, name="Abbau", capacity=3)
    c, sc = book(event, item, third, "c@x.de")
    services.join_waitlist(sc, slot2)
    djmail.outbox = []

    a.status = Order.STATUS_CANCELED
    a.save()
    services.cancel_order_signups(a)

    sb.refresh_from_db()
    sc.refresh_from_db()
    assert sb.slot == full_slot and sb.status == DutySignup.STATUS_ACTIVE
    assert sc.slot == slot2
    assert DutyWaitlistEntry.objects.get(signup=sb).status == "fulfilled"
    assert {m.to[0] for m in djmail.outbox} == {"b@x.de", "c@x.de"}
    assert any("Kuchen Montag" in m.body for m in djmail.outbox)
    assert b.all_logentries().filter(action_type="pretix_parentduties.waitlist.moved").exists()


def test_notify_does_not_move(event, item, full_slot, slot2, notify):
    a, sa = book(event, item, full_slot, "a@x.de")
    b, sb = book(event, item, slot2, "b@x.de", waitlist=[full_slot.pk])
    c, sc = book(event, item, slot2, "c@x.de", waitlist=[full_slot.pk])
    djmail.outbox = []
    services.cancel_order_signups(a)
    sb.refresh_from_db()
    assert sb.slot == slot2  # stays until the parents switch
    assert [m.to[0] for m in djmail.outbox] == ["b@x.de"]  # one free spot -> oldest only
    assert "Kuchen Montag" in djmail.outbox[0].subject
    assert page(b) in djmail.outbox[0].body
    entry = DutyWaitlistEntry.objects.get(signup=sb)
    assert entry.notified_at is not None and entry.status == "waiting"

    # b switches via the duty page -> entry fulfilled
    Client().post(page(b), {"signup": sb.pk, f"duty-{sb.pk}-slot": full_slot.pk, f"duty-{sb.pk}-helper_name": "B"})
    entry.refresh_from_db()
    assert entry.status == "fulfilled"
    # b's old slot2 had no waitlist, nothing else happens; c still waits for the (now full again) slot
    assert waiting(sc) == [full_slot.pk]


def test_notify_requires_deadline(event):
    event.settings.parentduties_waitlist_mode = "notify"
    assert not services.waitlist_active(event)


def test_inactive_after_deadline_or_event_start(event, item, full_slot, slot2, auto):
    event.settings.parentduties_change_until = RelativeDateWrapper(now() - datetime.timedelta(hours=1))
    assert not services.waitlist_active(event)
    del event.settings.parentduties_change_until
    assert services.waitlist_active(event)
    event.date_from = now() - datetime.timedelta(hours=1)
    event.save()
    assert not services.waitlist_active(event)


def test_customer_and_admin_changes_free_spots(event, item, full_slot, slot2, auto):
    event.settings.parentduties_change_until = RelativeDateWrapper(now() + datetime.timedelta(days=3))
    a, sa = book(event, item, full_slot, "a@x.de")
    b, sb = book(event, item, slot2, "b@x.de", waitlist=[full_slot.pk])
    services.customer_change(sa, slot2)  # a leaves the full slot
    sb.refresh_from_db()
    assert sb.slot == full_slot

    c, sc = book(event, item, slot2, "c@x.de")
    services.join_waitlist(sc, full_slot)
    services.admin_move(sb, slot2)  # admin moves b away again
    sc.refresh_from_db()
    assert sc.slot == full_slot


def test_capacity_increase_processes(event, item, full_slot, slot2, auto, admin_client,
                                     django_capture_on_commit_callbacks):
    a, sa = book(event, item, full_slot, "a@x.de")
    b, sb = book(event, item, slot2, "b@x.de", waitlist=[full_slot.pk])
    with django_capture_on_commit_callbacks(execute=True):
        r = admin_client.post(
            f"/control/event/{event.organizer.slug}/{event.slug}/parentduties/slots/{full_slot.pk}/",
            {"name_0": "Kuchen Montag", "capacity": "2", "position": "0", "active": "on"},
        )
    assert r.status_code == 302
    sb.refresh_from_db()
    assert sb.slot == full_slot


def test_canceled_order_leaves_waitlist(event, item, full_slot, slot2, auto):
    book(event, item, full_slot, "a@x.de")
    b, sb = book(event, item, slot2, "b@x.de", waitlist=[full_slot.pk])
    services.cancel_order_signups(b)
    assert DutyWaitlistEntry.objects.get(signup=sb).status == "removed"


def test_duty_page_join_and_leave(event, item, full_slot, slot2, notify):
    book(event, item, full_slot, "a@x.de")
    b, sb = book(event, item, slot2, "b@x.de")
    html = Client().get(page(b)).content.decode()
    assert "Join waitlist" in html
    Client().post(page(b), {"action": "join_waitlist", "signup": sb.pk, "waitlist_slot": full_slot.pk})
    assert waiting(sb) == [full_slot.pk]
    assert order_value("parent_duty_waitlist", b).startswith("Kuchen Montag")
    # not possible for a slot with free spots
    Client().post(page(b), {"action": "join_waitlist", "signup": sb.pk, "waitlist_slot": slot2.pk})
    assert waiting(sb) == [full_slot.pk]
    entry = DutyWaitlistEntry.objects.get(signup=sb)
    Client().post(page(b), {"action": "leave_waitlist", "entry": entry.pk})
    assert waiting(sb) == []


def test_foreign_entry_cannot_be_removed(event, item, full_slot, slot2, notify):
    book(event, item, full_slot, "a@x.de")
    b, sb = book(event, item, slot2, "b@x.de", waitlist=[full_slot.pk])
    c, _ = book(event, item, slot2, "c@x.de")
    entry = DutyWaitlistEntry.objects.get(signup=sb)
    Client().post(page(c), {"action": "leave_waitlist", "entry": entry.pk})
    assert waiting(sb) == [full_slot.pk]


def test_checkout_offers_waitlist(event, item, donation, full_slot, slot2, auto):
    q = Quota.objects.create(event=event, name="Alle", size=None)
    q.items.add(item)
    event.settings.attendee_names_asked = False
    book(event, item, full_slot, "a@x.de")
    c = Client()
    base = f"/{event.organizer.slug}/{event.slug}"
    c.post(base + "/cart/add", {f"item_{item.pk}": "1"})
    r = c.post(base + "/checkout/questions/", {"email": "b@x.de", "email_repeat": "b@x.de"}, follow=True)
    assert 'name="parentduties-waitlist"' in r.content.decode()
    # waitlist alone is not a duty
    r = c.post(base + "/checkout/parentduties/", {"parentduties-waitlist": str(full_slot.pk)})
    assert r.status_code == 200
    c.post(base + "/checkout/parentduties/", {
        "parentduties-duty": str(slot2.pk), "parentduties-waitlist": str(full_slot.pk),
        "parentduties-helper_name": "B",
    })
    c.post(base + "/checkout/confirm/", {"parentduties_commit": "yes"})
    order = Order.objects.get(email="b@x.de")
    assert waiting(order.parentduty_signups.get()) == [full_slot.pk]


def test_backend_waitlist_page(event, item, full_slot, slot2, auto, admin_client):
    book(event, item, full_slot, "a@x.de")
    b, sb = book(event, item, slot2, "b@x.de", waitlist=[full_slot.pk])
    base = f"/control/event/{event.organizer.slug}/{event.slug}/parentduties/"
    r = admin_client.get(base + "waitlist/")
    assert r.status_code == 200 and b.code in r.content.decode()
    entry = DutyWaitlistEntry.objects.get(signup=sb)
    admin_client.post(base + f"waitlist/{entry.pk}/remove")
    entry.refresh_from_db()
    assert entry.status == "removed"


def test_mail_in_order_language(event, item, full_slot, slot2, auto):
    event.settings.locales = ["en", "de"]
    a, _ = book(event, item, full_slot, "a@x.de")
    b, sb = book(event, item, slot2, "b@x.de", waitlist=[full_slot.pk])
    b.locale = "de"
    b.save()
    djmail.outbox = []
    services.cancel_order_signups(a)
    [m] = djmail.outbox
    assert m.subject.startswith("Euer Elterndienst hat sich geändert")
    assert "Kuchen Montag" in m.body and b.code in m.body
