"""Parents change their duty themselves until a deadline; deeplink {parent_duty_url}."""
import datetime
from urllib.parse import urlparse

import pytest
from django.test import Client
from django.utils.timezone import now

from pretix.base.reldate import RelativeDate, RelativeDateWrapper

from pretix_parentduties.models import DutySignup
from pretix_parentduties.placeholders import order_value
from pretix_parentduties.services import can_change, change_deadline, create_signups_for_order

from .conftest import make_order


@pytest.fixture
def open_deadline(event):
    event.settings.parentduties_change_until = RelativeDateWrapper(now() + datetime.timedelta(days=3))


def page(order):
    return f"/{order.event.organizer.slug}/{order.event.slug}/order/{order.code}/{order.secret}/parentduty/"


def booked(event, item, slot, **kw):
    order = make_order(event, item, **kw)
    create_signups_for_order(order, {"choice": "slot", "slots": [slot.pk], "helper_name": "Anna"})
    return order, order.parentduty_signups.get()


def change(order, signup, slot, **extra):
    data = {"signup": signup.pk, f"duty-{signup.pk}-slot": slot.pk,
            f"duty-{signup.pk}-helper_name": "Anna"}
    data.update(extra)
    return Client().post(page(order), data, follow=True)


def test_deadline_resolution(event):
    assert change_deadline(event) is None and not can_change(event)
    event.settings.parentduties_change_until = RelativeDateWrapper(now() - datetime.timedelta(minutes=1))
    assert not can_change(event)
    # relative to event start (30 days ahead): 7 days before is still open
    event.settings.parentduties_change_until = RelativeDateWrapper(RelativeDate(
        days=7, minutes=None, time=datetime.time(23, 59), base_date_name="date_from", is_after=False,
    ))
    assert can_change(event)


def test_page_without_deadline_is_read_only(event, item, slot, slot2):
    order, signup = booked(event, item, slot)
    r = Client().get(page(order))
    assert r.status_code == 200
    html = r.content.decode()
    assert "Kuchen Montag" in html
    assert "Change duty" not in html
    r = change(order, signup, slot2)
    assert "can no longer be changed" in r.content.decode()
    signup.refresh_from_db()
    assert signup.slot == slot


def test_wrong_secret(event, item, slot):
    order, _ = booked(event, item, slot)
    r = Client().get(f"/{event.organizer.slug}/{event.slug}/order/{order.code}/wrongsecret/parentduty/")
    assert r.status_code == 404


def test_change_until_deadline(event, item, slot, slot2, open_deadline):
    order, signup = booked(event, item, slot)
    html = Client().get(page(order)).content.decode()
    assert "Change duty" in html and "Aufbau Freitag" in html
    r = change(order, signup, slot2, **{f"duty-{signup.pk}-helper_name": "Ben"})
    assert "has been changed" in r.content.decode()
    signup.refresh_from_db()
    assert signup.slot == slot2 and signup.helper_name == "Ben"
    assert signup.status == DutySignup.STATUS_ACTIVE
    assert order.all_logentries().filter(action_type="pretix_parentduties.signup.changed_by_customer").exists()
    assert slot.occupied_count() == 0 and slot2.occupied_count() == 1


def test_no_change_after_deadline(event, item, slot, slot2):
    event.settings.parentduties_change_until = RelativeDateWrapper(now() - datetime.timedelta(hours=1))
    order, signup = booked(event, item, slot)
    change(order, signup, slot2)
    signup.refresh_from_db()
    assert signup.slot == slot


def test_full_slot_rejected(event, item, slot, slot2, open_deadline):
    slot2.capacity = 1
    slot2.save()
    booked(event, item, slot2, email="other@x.de")
    order, signup = booked(event, item, slot)
    r = change(order, signup, slot2)
    assert "already full" in r.content.decode()
    signup.refresh_from_db()
    assert signup.slot == slot


def test_withdrawal_impossible(event, item, slot, open_deadline):
    event.settings.parentduties_allow_cannot_help = True
    order, signup = booked(event, item, slot)
    for value in ("cannot_help", "skip", "sibling", ""):
        Client().post(page(order), {"signup": signup.pk, f"duty-{signup.pk}-slot": value,
                                    f"duty-{signup.pk}-helper_name": "Anna"})
    signup.refresh_from_db()
    assert signup.kind == DutySignup.KIND_SLOT and signup.slot == slot
    assert signup.status == DutySignup.STATUS_ACTIVE


def test_cannot_help_and_open_duty_can_take_a_slot(event, item, slot, open_deadline):
    order = make_order(event, item)
    [signup] = create_signups_for_order(order, {"choice": "cannot_help", "note": "Schicht"})
    change(order, signup, slot)
    signup.refresh_from_db()
    assert signup.kind == DutySignup.KIND_SLOT and signup.slot == slot and signup.note == ""

    order2 = make_order(event, item, email="neu@x.de")
    [open_duty] = create_signups_for_order(order2, None)
    assert open_duty.status == DutySignup.STATUS_NEEDS_REVIEW
    change(order2, open_duty, slot)
    open_duty.refresh_from_db()
    assert open_duty.slot == slot and open_duty.status == DutySignup.STATUS_ACTIVE


def test_sibling_and_exempt_not_changeable(event, item, slot, slot2, open_deadline):
    root_order, root = booked(event, item, slot)
    order = make_order(event, item)  # same e-mail -> sibling
    create_signups_for_order(order, None)
    sib = order.parentduty_signups.get()
    assert sib.kind == DutySignup.KIND_SIBLING
    html = Client().get(page(order)).content.decode()
    assert root_order.code in html and "Change duty" not in html
    change(order, sib, slot2)
    sib.refresh_from_db()
    assert sib.kind == DutySignup.KIND_SIBLING


def test_canceled_order_not_changeable(event, item, slot, slot2, open_deadline):
    from pretix.base.models import Order
    order, signup = booked(event, item, slot)
    order.status = Order.STATUS_CANCELED
    order.save()
    change(order, signup, slot2)
    signup.refresh_from_db()
    assert signup.slot == slot


def test_deeplink_placeholder_resolves(event, item, slot, open_deadline):
    order, _ = booked(event, item, slot)
    url = order_value("parent_duty_url", order)
    assert url.startswith("http") and order.secret in url
    r = Client().get(urlparse(url).path)
    assert r.status_code == 200 and "Kuchen Montag" in r.content.decode()

    empty = make_order(event, item)
    event.settings.parentduties_live = False
    assert order_value("parent_duty_url", empty) == ""


def test_order_page_links_to_duty(event, item, slot, open_deadline):
    order, _ = booked(event, item, slot)
    r = Client().get(f"/{event.organizer.slug}/{event.slug}/order/{order.code}/{order.secret}/")
    html = r.content.decode()
    assert page(order) in html and "View or change duty" in html


def test_multiple_duties_cannot_double_book(event, item, slot, slot2, open_deadline):
    event.settings.parentduties_allow_multiple = True
    order = make_order(event, item)
    s1, s2 = create_signups_for_order(order, {"choice": "slot", "slots": [slot.pk, slot2.pk]})
    change(order, s1, slot2)
    s1.refresh_from_db()
    assert s1.slot == slot


@pytest.mark.parametrize("locale,expected", [("en", "Change duty"), ("de", "Dienst ändern")])
def test_page_is_translated(event, item, slot, open_deadline, locale, expected):
    event.settings.locales = ["en", "de"]
    event.settings.locale = locale
    order, _ = booked(event, item, slot)
    order.locale = locale
    order.save()
    c = Client()
    c.cookies["pretix_language"] = locale
    assert expected in c.get(page(order)).content.decode()
