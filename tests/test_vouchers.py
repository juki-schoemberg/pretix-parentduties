"""Vouchers that exempt from the parent duty."""
import re

import pytest
from django.test import Client

from pretix.base.models import Order, OrderPosition, Quota, Voucher

from pretix_parentduties import services
from pretix_parentduties.models import DutySignup, VoucherDutyConfig
from pretix_parentduties.services import (
    create_signups_for_order, order_triggers, positions_trigger, waiving_vouchers,
)

from .conftest import make_order


@pytest.fixture
def exempt_voucher(event, item):
    v = Voucher.objects.create(event=event, code="ELTERNFREI", item=item, max_usages=10)
    VoucherDutyConfig.objects.create(voucher=v, skips_duty=True)
    return v


@pytest.fixture
def position_voucher(event, item):
    """Exempts only the position bought with it."""
    v = Voucher.objects.create(event=event, code="NURKIND", item=item, max_usages=10)
    VoucherDutyConfig.objects.create(voucher=v, skips_duty=True, whole_order=False)
    return v


@pytest.fixture
def staff_pass(event):
    from pretix.base.models import Item
    return Item.objects.create(event=event, name="Mitarbeiterausweis", default_price=0)  # no duty itself


@pytest.fixture
def staff_voucher(event, staff_pass):
    v = Voucher.objects.create(event=event, code="TEAM2027", item=staff_pass, max_usages=10)
    VoucherDutyConfig.objects.create(voucher=v, skips_duty=True, whole_order=True)
    return v


@pytest.fixture
def normal_voucher(event, item):
    return Voucher.objects.create(event=event, code="RABATT", item=item, max_usages=10)


def is_checked(html, name):
    tag = re.search(rf'<input[^>]*name="{name}"[^>]*>', html).group(0)
    return "checked" in tag


def vurl(event, path=""):
    return f"/control/event/{event.organizer.slug}/{event.slug}/vouchers/{path}"


def with_voucher(event, item, voucher, **kw):
    order = make_order(event, item, **kw)
    order.positions.update(voucher=voucher)
    return order


# ------------------------------------------------------------------ logic

def test_exempt_voucher_position_does_not_trigger(event, item, exempt_voucher, normal_voucher):
    assert not order_triggers(with_voucher(event, item, exempt_voucher))
    assert order_triggers(with_voucher(event, item, normal_voucher))
    assert order_triggers(make_order(event, item))


def test_disabled_config_does_not_exempt(event, item, exempt_voucher):
    VoucherDutyConfig.objects.filter(voucher=exempt_voucher).update(skips_duty=False)
    assert order_triggers(with_voucher(event, item, exempt_voucher))


def test_whole_order_voucher_exempts_mixed_order(event, item, exempt_voucher):
    """Child A with the voucher, child B without: whole_order exempts both."""
    order = with_voucher(event, item, exempt_voucher)
    OrderPosition.objects.create(order=order, item=item, price=0)
    assert not positions_trigger(event, order.positions.all())


def test_position_voucher_keeps_duty_for_other_positions(event, item, position_voucher):
    order = with_voucher(event, item, position_voucher)
    assert not order_triggers(order)
    OrderPosition.objects.create(order=order, item=item, price=0)
    assert positions_trigger(event, order.positions.all())


def test_staff_pass_exempts_children(event, item, staff_pass, staff_voucher):
    order = make_order(event, item)  # child ticket, triggers
    OrderPosition.objects.create(order=order, item=staff_pass, price=0, voucher=staff_voucher)
    assert not order_triggers(order)
    [s] = create_signups_for_order(order, None)
    assert s.voucher == staff_voucher
    assert waiving_vouchers(order) == ["TEAM2027"]


def test_staff_pass_alone_is_recorded(event, staff_pass, staff_voucher):
    """Staff pass first, children later: the exemption must exist for the code reference."""
    order = make_order(event, staff_pass)
    order.positions.update(voucher=staff_voucher)
    [s] = create_signups_for_order(order, None)
    assert s.voucher == staff_voucher


def test_later_order_covered_by_order_code(event, item, staff_pass, staff_voucher):
    staff = make_order(event, staff_pass, email="mama@x.de")
    staff.positions.update(voucher=staff_voucher)
    create_signups_for_order(staff, None)
    root = exempt_signup(staff)

    kids = make_order(event, item, email="papa@x.de")
    [s] = create_signups_for_order(kids, {"choice": "sibling", "sibling_code": staff.code.lower()})
    assert (s.kind, s.status, s.covered_by) == (DutySignup.KIND_SIBLING, DutySignup.STATUS_ACTIVE, root)
    assert services.family_code(s) == staff.code

    # canceling the staff order sends the covered order to review
    services.cancel_order_signups(staff)
    s.refresh_from_db()
    assert s.status == DutySignup.STATUS_NEEDS_REVIEW


def test_voucher_exemption_not_matched_by_email(event, item, staff_pass, staff_voucher):
    staff = make_order(event, staff_pass, email="familie@example.org")
    staff.positions.update(voucher=staff_voucher)
    create_signups_for_order(staff, None)
    assert services.find_family_signup(event, "familie@example.org") is None
    kids = make_order(event, item, email="familie@example.org")
    [s] = create_signups_for_order(kids, None)
    assert s.kind == DutySignup.KIND_SLOT and s.status == DutySignup.STATUS_NEEDS_REVIEW


def test_order_change_keeps_or_reviews_exemption(event, item, staff_pass, staff_voucher):
    order = make_order(event, item)
    OrderPosition.objects.create(order=order, item=staff_pass, price=0, voucher=staff_voucher)
    create_signups_for_order(order, None)
    services.handle_order_changed(order)
    assert exempt_signup(order)
    order.positions.filter(item=staff_pass).delete()  # staff pass removed, child remains
    services.handle_order_changed(order)
    assert order.parentduty_signups.get().status == DutySignup.STATUS_NEEDS_REVIEW


def exempt_signup(order):
    s = order.parentduty_signups.get()
    assert (s.kind, s.status) == (DutySignup.KIND_EXEMPT, DutySignup.STATUS_ACTIVE)
    return s


def test_exempt_order_gets_voucher_exemption(event, item, slot, exempt_voucher):
    order = with_voucher(event, item, exempt_voucher)
    # even a (stale) slot choice from the session does not create a duty
    [s] = create_signups_for_order(order, {"choice": "slot", "slots": [slot.pk]})
    assert s == exempt_signup(order) and s.voucher == exempt_voucher
    assert slot.occupied_count() == 0
    assert waiving_vouchers(order) == ["ELTERNFREI"]
    from pretix_parentduties.placeholders import order_value
    assert order_value("parent_duty", order) == "exempt (voucher ELTERNFREI)"


# --------------------------------------------------------------- checkout

@pytest.fixture
def shop(event, item, staff_pass):
    q = Quota.objects.create(event=event, name="Alle", size=None)
    q.items.add(item, staff_pass)
    event.settings.attendee_names_asked = False
    return event


def checkout(event, item, voucher_code=None, email="familie@example.org"):
    c = Client()
    base = f"/{event.organizer.slug}/{event.slug}"
    data = {f"item_{item.pk}": "1"}
    if voucher_code:
        data["_voucher_code"] = voucher_code
    c.post(base + "/cart/add", data, follow=True)
    r = c.post(base + "/checkout/questions/", {"email": email, "email_repeat": email}, follow=True)
    return c, base, r


def test_checkout_with_exempt_voucher_skips_step(shop, item, slot, exempt_voucher):
    c, base, r = checkout(shop, item, "ELTERNFREI")
    assert not r.redirect_chain[-1][0].endswith("/checkout/parentduties/")
    assert "Kuchen Montag" not in r.content.decode()
    c.post(base + "/checkout/confirm/", {})
    order = Order.objects.get(event=shop)
    assert order.positions.get().voucher == exempt_voucher
    assert exempt_signup(order).voucher == exempt_voucher


def test_checkout_staff_pass_with_children(shop, item, staff_pass, slot, staff_voucher):
    c = Client()
    base = f"/{shop.organizer.slug}/{shop.slug}"
    c.post(base + "/cart/add", {f"item_{item.pk}": "2"}, follow=True)  # two children
    c.post(base + "/cart/add", {f"item_{staff_pass.pk}": "1", "_voucher_code": "TEAM2027"}, follow=True)
    r = c.post(base + "/checkout/questions/", {"email": "team@x.de", "email_repeat": "team@x.de"}, follow=True)
    assert not r.redirect_chain[-1][0].endswith("/checkout/parentduties/")
    c.post(base + "/checkout/confirm/", {})
    order = Order.objects.get(event=shop)
    assert order.positions.count() == 3
    assert exempt_signup(order).voucher == staff_voucher

    # later the other parent registers another child and enters the staff order's code
    c2 = Client()
    c2.post(base + "/cart/add", {f"item_{item.pk}": "1"}, follow=True)
    r = c2.post(base + "/checkout/questions/", {"email": "papa@x.de", "email_repeat": "papa@x.de"}, follow=True)
    assert r.redirect_chain[-1][0].endswith("/checkout/parentduties/")
    r = c2.post(base + "/checkout/parentduties/", {"parentduties-duty": "sibling",
                                                   "parentduties-sibling_code": order.code}, follow=True)
    assert r.redirect_chain[-1][0].endswith("/checkout/confirm/")
    c2.post(base + "/checkout/confirm/", {})
    later = Order.objects.exclude(pk=order.pk).get(event=shop)
    s = later.parentduty_signups.get()
    assert s.status == DutySignup.STATUS_ACTIVE and s.covered_by.order == order


def test_checkout_position_voucher_with_second_child(shop, item, slot, position_voucher):
    c = Client()
    base = f"/{shop.organizer.slug}/{shop.slug}"
    c.post(base + "/cart/add", {f"item_{item.pk}": "1", "_voucher_code": "NURKIND"}, follow=True)
    c.post(base + "/cart/add", {f"item_{item.pk}": "1"}, follow=True)
    r = c.post(base + "/checkout/questions/", {"email": "f@x.de", "email_repeat": "f@x.de"}, follow=True)
    assert r.redirect_chain[-1][0].endswith("/checkout/parentduties/")


def test_checkout_with_normal_voucher_keeps_step(shop, item, slot, normal_voucher):
    c, base, r = checkout(shop, item, "RABATT")
    assert r.redirect_chain[-1][0].endswith("/checkout/parentduties/")


# ---------------------------------------------------------------- backend

def test_voucher_form_checkbox_create_and_edit(event, item, admin_client):
    r = admin_client.get(vurl(event, "add"))
    assert r.status_code == 200
    html = r.content.decode()
    assert 'name="parentduties_skips_duty"' in html
    # "whole order" is pre-checked for new vouchers
    assert is_checked(html, "parentduties_whole_order")

    r = admin_client.post(vurl(event, "add"), {
        "code": "TEAMKIND", "min_usages": "1", "max_usages": "1", "valid_until_0": "", "valid_until_1": "",
        "block_quota": "", "allow_ignore_quota": "", "price_mode": "none", "value": "",
        "itemvar": f"{item.pk}", "parentduties_skips_duty": "on",
    })
    assert r.status_code == 302, r.content.decode()[:3000]
    v = Voucher.objects.get(code="TEAMKIND")
    assert v.parentduty_config.skips_duty
    assert not v.parentduty_config.whole_order  # second checkbox not sent

    html = admin_client.get(vurl(event, f"{v.pk}/")).content.decode()
    assert is_checked(html, "parentduties_skips_duty")

    r = admin_client.post(vurl(event, f"{v.pk}/"), {
        "code": "TEAMKIND", "min_usages": "1", "max_usages": "1", "valid_until_0": "", "valid_until_1": "",
        "price_mode": "none", "value": "", "itemvar": f"{item.pk}",
    })
    assert r.status_code == 302, r.content.decode()[:3000]
    assert not VoucherDutyConfig.objects.filter(voucher=v).exists()


def test_voucher_bulk_create(event, item, admin_client):
    r = admin_client.post(vurl(event, "bulk_add"), {
        "codes": "BULK1\nBULK2", "min_usages": "1", "max_usages": "1", "valid_until_0": "", "valid_until_1": "",
        "price_mode": "none", "value": "", "itemvar": f"{item.pk}", "send": "",
        "parentduties_skips_duty": "on", "parentduties_whole_order": "on",
    })
    assert r.status_code in (200, 302), r.content.decode()[:3000]
    assert set(VoucherDutyConfig.objects.filter(skips_duty=True, whole_order=True).values_list(
        "voucher__code", flat=True)) == {"BULK1", "BULK2"}


def test_no_checkbox_without_plugin(event, admin_client):
    event.disable_plugin("pretix_parentduties")
    event.save()
    assert 'name="parentduties_skips_duty"' not in admin_client.get(vurl(event, "add")).content.decode()


def test_order_box_shows_exemption(event, item, exempt_voucher, admin_client):
    order = with_voucher(event, item, exempt_voucher)
    create_signups_for_order(order, None)
    html = admin_client.get(f"/control/event/{event.organizer.slug}/{event.slug}/orders/{order.code}/").content.decode()
    assert "exempt (voucher ELTERNFREI)" in html
