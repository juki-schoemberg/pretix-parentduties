"""Categories and the manually defined order of duties (backend → checkout)."""
import json
import re

import pytest
from django.test import Client

from pretix.base.models import Event, Quota

from pretix_parentduties import services
from pretix_parentduties.models import DutyCategory, DutySlot


@pytest.fixture
def setup(event, category_cake, category_setup):
    """Setup (pos 0) with two slots, Cake (pos 1) with two slots, one slot without category."""
    s = {}
    s["fr"] = DutySlot.objects.create(event=event, name="Aufbau Fr", category=category_setup, position=1)
    s["sa"] = DutySlot.objects.create(event=event, name="Aufbau Sa", category=category_setup, position=0)
    s["mo"] = DutySlot.objects.create(event=event, name="Kuchen Mo", category=category_cake, position=0)
    s["di"] = DutySlot.objects.create(event=event, name="Kuchen Di", category=category_cake, position=1)
    s["x"] = DutySlot.objects.create(event=event, name="Sonstiges", category=None, position=0)
    return s


def url(event, path=""):
    return f"/control/event/{event.organizer.slug}/{event.slug}/parentduties/{path}"


def names(slots):
    return [str(s.name) for s in slots]


def test_display_order(event, setup):
    slots = services.ordered_slots(DutySlot.objects.filter(event=event))
    assert names(slots) == ["Aufbau Sa", "Aufbau Fr", "Kuchen Mo", "Kuchen Di", "Sonstiges"]
    groups = services.group_by_category(slots)
    assert [str(c.name) if c else None for c, _ in groups] == ["Aufbau", "Kuchen", None]


def test_checkout_grouped_by_category_in_order(event, item, setup):
    q = Quota.objects.create(event=event, name="Alle", size=None)
    q.items.add(item)
    event.settings.attendee_names_asked = False
    c = Client()
    base = f"/{event.organizer.slug}/{event.slug}"
    c.post(base + "/cart/add", {f"item_{item.pk}": "1"})
    r = c.post(base + "/checkout/questions/", {"email": "a@x.de", "email_repeat": "a@x.de"}, follow=True)
    html = r.content.decode()
    headings = re.findall(r'<h3 class="h3" id="ppd-category-[^"]+">([^<]+)</h3>', html)
    assert [h.strip() for h in headings] == ["Aufbau", "Kuchen", "Other duties"]
    order = [m for m in re.findall(r'<h4 class="h4" id="ppd-slot-\d+-legend">([^<]+)</h4>', html)]
    assert order == ["Aufbau Sa", "Aufbau Fr", "Kuchen Mo", "Kuchen Di", "Sonstiges"]
    assert "style=" not in html.split('class="parentduties-slots"')[1].split("checkout-button-row")[0]


def test_slot_reorder_into_other_category(event, setup, admin_client, category_cake):
    s = setup
    ids = [s["mo"].pk, s["fr"].pk, s["di"].pk]
    r = admin_client.post(url(event, f"slots/reorder/{category_cake.pk}/"), json.dumps({"ids": [str(i) for i in ids]}),
                          content_type="application/json")
    assert r.status_code == 200
    for x in s.values():
        x.refresh_from_db()
    assert (s["mo"].position, s["fr"].position, s["di"].position) == (0, 1, 2)
    assert s["fr"].category == category_cake
    assert event.logentry_set.filter(action_type="pretix_parentduties.slots.reordered").exists()


def test_slot_reorder_to_uncategorized_and_invalid(event, setup, admin_client, organizer):
    s = setup
    r = admin_client.post(url(event, "slots/reorder/0/"), json.dumps({"ids": [str(s["x"].pk), str(s["sa"].pk)]}),
                          content_type="application/json")
    assert r.status_code == 200
    s["sa"].refresh_from_db()
    assert s["sa"].category is None and s["sa"].position == 1
    assert admin_client.post(url(event, "slots/reorder/0/"), "nope",
                             content_type="application/json").status_code == 400
    other = Event.objects.create(organizer=organizer, name="X", slug="x", date_from=event.date_from)
    foreign = DutySlot.objects.create(event=other, name="Fremd")
    r = admin_client.post(url(event, "slots/reorder/0/"), json.dumps({"ids": [str(foreign.pk)]}),
                          content_type="application/json")
    assert r.status_code == 404


def test_slot_arrows(event, setup, admin_client):
    s = setup
    admin_client.post(url(event, f"slots/{s['fr'].pk}/move/up"))
    s["fr"].refresh_from_db()
    s["sa"].refresh_from_db()
    assert (s["fr"].position, s["sa"].position) == (0, 1)
    admin_client.post(url(event, f"slots/{s['fr'].pk}/move/up"))  # already first: no change
    s["fr"].refresh_from_db()
    assert s["fr"].position == 0


def test_new_slot_and_category_change_append_at_end(event, setup, admin_client, category_cake):
    r = admin_client.post(url(event, "slots/add"), {"name_0": "Kuchen Mi", "category": category_cake.pk,
                                                    "active": "on"})
    assert r.status_code == 302, r.content.decode()[:2000]
    new = DutySlot.objects.get(name__icontains="Kuchen Mi")
    assert new.category == category_cake and new.position == 2
    s = setup
    admin_client.post(url(event, f"slots/{s['x'].pk}/"), {"name_0": "Sonstiges", "category": category_cake.pk,
                                                          "active": "on"})
    s["x"].refresh_from_db()
    assert s["x"].category == category_cake and s["x"].position == 3


def test_slot_list_page(event, setup, admin_client):
    html = admin_client.get(url(event)).content.decode()
    assert html.index("Aufbau Sa") < html.index("Aufbau Fr") < html.index("Kuchen Mo") < html.index("Sonstiges")
    assert 'data-dnd-group="slots"' in html
    assert "pretix_parentduties/control.css" in html


def test_category_crud_and_order(event, setup, admin_client, category_cake, category_setup):
    assert admin_client.get(url(event, "categories/")).status_code == 200
    r = admin_client.post(url(event, "categories/add"), {"name_0": "Abbau", "description_0": ""})
    assert r.status_code == 302
    abbau = DutyCategory.objects.get(event=event, name__icontains="Abbau")
    assert abbau.position == 2

    admin_client.post(url(event, f"categories/{abbau.pk}/move/up"))
    cats = list(DutyCategory.objects.filter(event=event).order_by("position"))
    assert [str(c.name) for c in cats] == ["Aufbau", "Abbau", "Kuchen"]

    ids = [str(category_cake.pk), str(abbau.pk), str(category_setup.pk)]
    r = admin_client.post(url(event, "categories/reorder"), json.dumps({"ids": ids}), content_type="application/json")
    assert r.status_code == 200
    assert [str(c.name) for c in DutyCategory.objects.filter(event=event).order_by("position")] == [
        "Kuchen", "Abbau", "Aufbau",
    ]
    # incomplete list is refused
    r = admin_client.post(url(event, "categories/reorder"), json.dumps({"ids": ids[:2]}),
                          content_type="application/json")
    assert r.status_code == 404

    admin_client.post(url(event, f"categories/{category_cake.pk}/delete"))
    setup["mo"].refresh_from_db()
    assert setup["mo"].category is None


def test_event_copy_keeps_categories(event, organizer, setup):
    from pretix_parentduties.signals import copy_event_data
    new = Event.objects.create(organizer=organizer, name="2028", slug="s28", date_from=event.date_from,
                               plugins="pretix_parentduties")
    copy_event_data(sender=new, other=event, item_map={})
    groups = services.group_by_category(services.ordered_slots(DutySlot.objects.filter(event=new)))
    assert [(str(c.name) if c else None, names(s)) for c, s in groups] == [
        ("Aufbau", ["Aufbau Sa", "Aufbau Fr"]), ("Kuchen", ["Kuchen Mo", "Kuchen Di"]), (None, ["Sonstiges"]),
    ]
    assert all(c.event == new for c, _ in groups if c)


def test_placeholder_category(event, item, setup):
    from pretix_parentduties.placeholders import order_value

    from .conftest import make_order
    order = make_order(event, item)
    services.create_signups_for_order(order, {"choice": "slot", "slots": [setup["mo"].pk]})
    assert order_value("parent_duty_category", order) == "Kuchen"


def test_presale_css_included(event):
    html = Client().get(f"/{event.organizer.slug}/{event.slug}/").content.decode()
    assert "pretix_parentduties/presale.css" in html
