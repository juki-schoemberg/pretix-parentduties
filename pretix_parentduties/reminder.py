"""Ready-made reminder e-mail as a scheduled rule of pretix' "Send out emails" module."""
import datetime

from django.utils.translation import gettext_noop
from i18nfield.strings import LazyI18nString

from .services import triggering_item_ids

SENDMAIL_PLUGIN = "pretix.plugins.sendmail"

REMINDER_SUBJECT = gettext_noop("Reminder: your parent duty at {event}")
REMINDER_TEXT = gettext_noop(
    "Hello,\n\n"
    "{event} is coming up soon – thank you for lending a hand! This is your parent duty:\n\n"
    "{parent_duty}\n"
    "{parent_duty_description}\n\n"
    "You can find all details here:\n"
    "{parent_duty_url}\n\n"
    "If something has come up, please let us know as soon as possible.\n\n"
    "See you soon!\n"
    "{event}"
)


class NoTriggeringProducts(Exception):
    pass


def create_reminder_rule(event, days_before=7, time_of_day=datetime.time(10, 0), user=None):
    """Create an enabled scheduled e-mail rule ``days_before`` days before the event.

    The rule is limited to the products that trigger the parent duty, so donations and
    merchandise orders do not get it. The sendmail plugin is enabled if necessary.
    """
    from pretix.plugins.sendmail.models import Rule

    items = list(event.items.filter(pk__in=triggering_item_ids(event)))
    if not items:
        raise NoTriggeringProducts()
    if SENDMAIL_PLUGIN not in event.get_plugins():
        event.enable_plugin(SENDMAIL_PLUGIN)
        event.save()
    rule = Rule(
        event=event,
        subject=LazyI18nString.from_gettext(REMINDER_SUBJECT),
        template=LazyI18nString.from_gettext(REMINDER_TEXT),
        all_products=False,
        date_is_absolute=False,
        send_offset_days=days_before,
        send_offset_time=time_of_day,
        offset_is_after=False,
        offset_to_event_end=False,
        send_to=Rule.CUSTOMERS,
        enabled=True,
    )
    rule.save()
    rule.limit_products.set(items)
    event.log_action("pretix_parentduties.reminder.created", user=user,
                     data={"rule": rule.pk, "days_before": days_before})
    return rule
