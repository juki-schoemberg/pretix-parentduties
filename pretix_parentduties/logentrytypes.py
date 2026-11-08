from django.utils.translation import gettext_lazy as _

from pretix.base.logentrytypes import (
    EventLogEntryType, OrderLogEntryType, log_entry_types,
)


@log_entry_types.new_from_dict({
    "pretix_parentduties.signup.created": _("Parent duty registered ({kind}, {status})."),
    "pretix_parentduties.signup.overbooked": _(
        "Parent duty registered, but the duty was already full – needs review."
    ),
    "pretix_parentduties.signup.canceled": _("Parent duty canceled."),
    "pretix_parentduties.signup.reactivated": _("Parent duty reactivated ({status})."),
    "pretix_parentduties.signup.needs_review": _("Parent duty needs review ({reason})."),
    "pretix_parentduties.signup.moved": _("Parent duty moved to another duty."),
    "pretix_parentduties.signup.changed": _("Parent duty changed ({kind}, {status})."),
    "pretix_parentduties.signup.changed_by_customer": _("Parent duty changed by the customer to “{slot_name}”."),
})
class ParentDutyOrderLogEntryType(OrderLogEntryType):
    pass


@log_entry_types.new_from_dict({
    "pretix_parentduties.slot.created": _("Parent duty slot “{name}” created."),
    "pretix_parentduties.slot.changed": _("Parent duty slot “{name}” changed."),
    "pretix_parentduties.slot.deleted": _("Parent duty slot “{name}” deleted."),
    "pretix_parentduties.items.changed": _("Products triggering the parent duty changed."),
})
class ParentDutyEventLogEntryType(EventLogEntryType):
    pass
