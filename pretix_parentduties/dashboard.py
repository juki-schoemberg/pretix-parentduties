from django.db.models import Q
from django.template.loader import get_template
from django.urls import reverse

from .models import DutySignup, DutySlot


def stats(event, subevent=None):
    slots = DutySlot.objects.filter(event=event, active=True)
    if subevent:
        slots = slots.filter(Q(subevent=subevent) | Q(subevent__isnull=True))
    slots = list(slots.with_occupancy())
    capacity = sum(s.capacity for s in slots if s.capacity is not None)
    occupied_limited = sum(min(s.occupied, s.capacity) for s in slots if s.capacity is not None)
    occupied = sum(s.occupied for s in slots)
    review = DutySignup.objects.filter(
        event=event,
    ).filter(
        Q(status=DutySignup.STATUS_NEEDS_REVIEW)
        | Q(kind=DutySignup.KIND_CANNOT_HELP, status=DutySignup.STATUS_ACTIVE)
    ).count()
    return {
        "slots": slots,
        "capacity": capacity,
        "occupied": occupied,
        "occupied_limited": occupied_limited,
        "unlimited": any(s.capacity is None for s in slots),
        "review": review,
        "full": [s for s in slots if s.is_full()],
        "empty": [s for s in slots if s.occupied == 0],
    }


def widget(event, subevent=None, lazy=False):
    url = reverse("plugins:pretix_parentduties:slots", kwargs={
        "organizer": event.organizer.slug, "event": event.slug,
    })
    content = None
    if not lazy:
        content = get_template("pretix_parentduties/dashboard_widget.html").render(stats(event, subevent))
    return [{
        "content": content,
        "lazy": "pretix-parentduties",
        "display_size": "small",
        "priority": 50,
        "url": url,
    }]
