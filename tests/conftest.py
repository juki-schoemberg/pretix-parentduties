import datetime

import pytest
from django.utils import translation
from django.utils.timezone import now
from django_scopes import scopes_disabled


@pytest.fixture(autouse=True)
def no_scope(db):
    """pretix scopes almost all managers; disable that for the tests."""
    with scopes_disabled():
        yield


@pytest.fixture(autouse=True)
def reset_language():
    """Requests in German must not leak the active language into later tests."""
    yield
    translation.activate("en")


@pytest.fixture
def organizer(db):
    from pretix.base.models import Organizer
    return Organizer.objects.create(name="JuKi", slug="juki")


@pytest.fixture
def event(organizer):
    from pretix.base.models import Event
    ev = Event.objects.create(
        organizer=organizer,
        name="Schömsala",
        slug="schoemsala",
        date_from=now() + datetime.timedelta(days=30),
        plugins="pretix_parentduties",
        live=True,
    )
    ev.settings.parentduties_live = True
    return ev


@pytest.fixture
def item(event):
    from pretix.base.models import Item

    from pretix_parentduties.models import ItemDutyConfig
    it = Item.objects.create(event=event, name="Kind", default_price=0)
    ItemDutyConfig.objects.create(item=it, triggers_duty=True)
    return it


@pytest.fixture
def donation(event):
    from pretix.base.models import Item
    return Item.objects.create(event=event, name="Spende", default_price=10)


@pytest.fixture
def slot(event):
    from pretix_parentduties.models import DutySlot
    return DutySlot.objects.create(
        event=event, name="Kuchen Montag", tag="Kuchen", capacity=2,
        date=datetime.date(2027, 7, 12), time_from=datetime.time(14), time_to=datetime.time(16),
    )


@pytest.fixture
def slot2(event):
    from pretix_parentduties.models import DutySlot
    return DutySlot.objects.create(event=event, name="Aufbau Freitag", tag="Aufbau", capacity=5)


_counter = {"n": 0}


def make_order(event, item, code=None, email="familie@example.org", status=None):
    from pretix.base.models import Order, OrderPosition
    _counter["n"] += 1
    o = Order.objects.create(
        event=event,
        code=code or f"T{_counter['n']:04d}",
        status=status or Order.STATUS_PENDING,
        datetime=now(),
        expires=now() + datetime.timedelta(days=10),
        total=0,
        email=email,
        sales_channel=event.organizer.sales_channels.first(),
    )
    OrderPosition.objects.create(order=o, item=item, price=0, attendee_name_parts={"full_name": "Kind " + o.code})
    return o
