"""End-to-end through the real pretix checkout, also in the widget (cart namespace)."""
import re

import pytest
from django.test import Client

from pretix.base.models import Order, Quota

from pretix_parentduties.models import DutySignup

from .conftest import make_order


@pytest.fixture
def shop(event, item, donation):
    q = Quota.objects.create(event=event, name="Alle", size=None)
    q.items.add(item, donation)
    event.settings.attendee_names_asked = False
    event.settings.parentduties_ask_phone = "off"
    return event


def base(event, widget=False):
    prefix = f"/{event.organizer.slug}/{event.slug}"
    return prefix + ("/w/abcdefghijklmnop" if widget else "")


def fill_cart(client, event, item, widget=False, email="familie@example.org"):
    r = client.post(base(event, widget) + "/cart/add", {f"item_{item.pk}": "1"}, follow=True)
    assert r.status_code == 200
    r = client.post(base(event, widget) + "/checkout/questions/", {
        "email": email, "email_repeat": email,
    }, follow=True)
    assert r.status_code == 200
    return r


def step_url(event, widget=False):
    return base(event, widget) + "/checkout/parentduties/"


def confirm(client, event, widget=False, commit=True):
    data = {"parentduties_commit": "yes"} if commit else {}
    return client.post(base(event, widget) + "/checkout/confirm/", data, follow=True)


@pytest.mark.parametrize("widget", [False, True])
def test_full_checkout_with_slot(shop, item, slot, widget):
    c = Client()
    r = fill_cart(c, shop, item, widget)
    # After the questions, the parent duty step comes next
    assert r.redirect_chain[-1][0].endswith("/checkout/parentduties/")
    assert "Kuchen Montag" in r.content.decode()

    r = c.post(step_url(shop, widget), {"parentduties-duty": str(slot.pk),
                                        "parentduties-helper_name": "Anna Muster"}, follow=True)
    assert r.redirect_chain[-1][0].endswith("/checkout/confirm/")
    html = r.content.decode()
    assert "Kuchen Montag" in html
    assert 'name="parentduties_commit"' in html

    r = confirm(c, shop, widget)
    order = Order.objects.get(event=shop)
    s = order.parentduty_signups.get()
    assert s.slot == slot and s.status == DutySignup.STATUS_ACTIVE
    assert s.helper_name == "Anna Muster"
    assert "Kuchen Montag" in r.content.decode()  # order page box


def test_commit_checkbox_required(shop, item, slot):
    c = Client()
    fill_cart(c, shop, item)
    c.post(step_url(shop), {"parentduties-duty": str(slot.pk), "parentduties-helper_name": "Anna"})
    confirm(c, shop, commit=False)
    assert not Order.objects.filter(event=shop).exists()


def test_step_hidden_for_donation_only(shop, donation, slot):
    donation.default_price = 0  # avoid the payment step in this test
    donation.save()
    c = Client()
    r = fill_cart(c, shop, donation)
    assert not r.redirect_chain[-1][0].endswith("/checkout/parentduties/")
    confirm(c, shop, commit=False)
    order = Order.objects.get(event=shop)
    assert not order.parentduty_signups.exists()


def test_step_hidden_when_not_live(shop, item, slot):
    shop.settings.parentduties_live = False
    c = Client()
    r = fill_cart(c, shop, item)
    assert not r.redirect_chain[-1][0].endswith("/checkout/parentduties/")


def test_family_known(shop, item, slot):
    first = make_order(shop, item, email="familie@example.org")
    from pretix_parentduties.services import create_signups_for_order
    create_signups_for_order(first, {"choice": "slot", "slots": [slot.pk]})

    c = Client()
    r = fill_cart(c, shop, item, email="FAMILIE@example.org")
    html = r.content.decode()
    assert "already registered for your family" in html
    assert "Kuchen Montag" not in html  # no details for strangers
    r = c.post(step_url(shop), {}, follow=True)
    assert r.redirect_chain[-1][0].endswith("/checkout/confirm/")
    confirm(c, shop, commit=False)
    order = Order.objects.exclude(pk=first.pk).get(event=shop)
    s = order.parentduty_signups.get()
    assert s.kind == DutySignup.KIND_SIBLING and s.covered_by.order == first


def test_full_slot_disabled_and_rejected(shop, item, slot, slot2):
    slot.capacity = 1
    slot.save()
    from pretix_parentduties.services import create_signups_for_order
    create_signups_for_order(make_order(shop, item, email="x@y.de"), {"choice": "slot", "slots": [slot.pk]})

    c = Client()
    r = fill_cart(c, shop, item)
    assert re.search(rf'value="{slot.pk}"\s+disabled', r.content.decode())
    assert not re.search(rf'value="{slot2.pk}"\s+disabled', r.content.decode())
    r = c.post(step_url(shop), {"parentduties-duty": str(slot.pk), "parentduties-helper_name": "A"})
    assert r.status_code == 200  # form re-rendered with error
    assert "already full" in r.content.decode()


def test_slot_fills_up_before_confirm(shop, item, slot, slot2):
    slot.capacity = 1
    slot.save()
    c = Client()
    fill_cart(c, shop, item)
    c.post(step_url(shop), {"parentduties-duty": str(slot.pk), "parentduties-helper_name": "A"})
    # someone else is faster
    from pretix_parentduties.services import create_signups_for_order
    create_signups_for_order(make_order(shop, item, email="x@y.de"), {"choice": "slot", "slots": [slot.pk]})
    r = confirm(c, shop)
    assert Order.objects.filter(event=shop).count() == 1  # only the other one
    assert r.redirect_chain[-1][0].endswith("/checkout/parentduties/")


def test_sibling_code_and_validation(shop, item, slot):
    c = Client()
    fill_cart(c, shop, item)
    r = c.post(step_url(shop), {"parentduties-duty": "sibling"})
    assert "Please enter the order code" in r.content.decode()
    r = c.post(step_url(shop), {"parentduties-duty": "sibling", "parentduties-sibling_code": "ab123"}, follow=True)
    assert r.redirect_chain[-1][0].endswith("/checkout/confirm/")
    confirm(c, shop, commit=False)
    s = DutySignup.objects.get()
    assert s.kind == DutySignup.KIND_SIBLING and s.status == DutySignup.STATUS_NEEDS_REVIEW
    assert s.sibling_code == "AB123"


def test_name_required(shop, item, slot):
    c = Client()
    fill_cart(c, shop, item)
    r = c.post(step_url(shop), {"parentduties-duty": str(slot.pk)})
    assert r.status_code == 200
    assert "This field is required" in r.content.decode()


def test_cannot_help_option(shop, item, slot):
    c = Client()
    fill_cart(c, shop, item)
    r = c.post(step_url(shop), {"parentduties-duty": "cannot_help"})
    assert r.status_code == 200  # not offered
    shop.settings.parentduties_allow_cannot_help = True
    r = c.post(step_url(shop), {"parentduties-duty": "cannot_help"})
    assert "Please tell us briefly why" in r.content.decode()
    r = c.post(step_url(shop), {"parentduties-duty": "cannot_help", "parentduties-reason": "Krank"}, follow=True)
    assert r.redirect_chain[-1][0].endswith("/checkout/confirm/")


def test_multiple(shop, item, slot, slot2):
    shop.settings.parentduties_allow_multiple = True
    shop.settings.parentduties_max_per_family = 1
    c = Client()
    fill_cart(c, shop, item)
    r = c.post(step_url(shop), {"parentduties-duty": [str(slot.pk), str(slot2.pk)], "parentduties-helper_name": "A"})
    assert "at most 1" in r.content.decode()
    shop.settings.parentduties_max_per_family = 2
    r = c.post(step_url(shop), {"parentduties-duty": [str(slot.pk), str(slot2.pk)], "parentduties-helper_name": "A"},
               follow=True)
    assert r.redirect_chain[-1][0].endswith("/checkout/confirm/")
    confirm(c, shop)
    assert DutySignup.objects.filter(kind="slot", status="active").count() == 2
