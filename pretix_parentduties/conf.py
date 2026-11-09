"""Event settings of the plugin (settings store, prefix ``parentduties_``)."""
from django.utils.translation import gettext_noop
from i18nfield.strings import LazyI18nString

from pretix.base.reldate import RelativeDateWrapper
from pretix.base.settings import settings_hierarkey

ASK_OFF = "off"
ASK_OPTIONAL = "optional"
ASK_REQUIRED = "required"

WAITLIST_OFF = "off"
WAITLIST_NOTIFY = "notify"
WAITLIST_AUTO = "auto"

DEFAULTS = {
    "parentduties_live": (False, bool),
    "parentduties_required": (True, bool),
    "parentduties_title": (
        LazyI18nString.from_gettext(gettext_noop("Parent duty")),
        LazyI18nString,
    ),
    "parentduties_intro": (
        LazyI18nString.from_gettext(gettext_noop(
            "Our events only work because families lend a hand. Please pick one duty your family "
            "would like to take on – it only takes a minute, and you only need to do it once per family."
        )),
        LazyI18nString,
    ),
    "parentduties_allow_cannot_help": (False, bool),
    "parentduties_cannot_help_reason_required": (True, bool),
    "parentduties_sibling_code": (True, bool),
    "parentduties_allow_multiple": (False, bool),
    "parentduties_max_per_family": (2, int),
    "parentduties_ask_name": (ASK_REQUIRED, str),
    "parentduties_ask_phone": (ASK_OPTIONAL, str),
    "parentduties_confirm_checkbox": (True, bool),
    "parentduties_new_items_trigger": (False, bool),
    # Parents may change (never withdraw) their duty until this date; None = admins only.
    "parentduties_change_until": (None, RelativeDateWrapper),
    # Waitlist for full slots: off, notify (e-mail, parents switch themselves) or auto (moved automatically)
    "parentduties_waitlist_mode": (WAITLIST_OFF, str),
    "parentduties_waitlist_auto_subject": (
        LazyI18nString.from_gettext(gettext_noop("Your parent duty has changed: {parent_duty_name}")),
        LazyI18nString,
    ),
    "parentduties_waitlist_auto_text": (
        LazyI18nString.from_gettext(gettext_noop(
            "Hello,\n\n"
            "good news: a spot has become free in the duty you were waiting for. We have moved your "
            "parent duty for order {code} there:\n\n"
            "{parent_duty}\n\n"
            "Your previous duty is no longer reserved for you. All details:\n"
            "{parent_duty_url}\n\n"
            "Thank you for lending a hand!\n"
            "{event}"
        )),
        LazyI18nString,
    ),
    "parentduties_waitlist_notify_subject": (
        LazyI18nString.from_gettext(gettext_noop("A spot is free: {parent_duty_waitlist_slot}")),
        LazyI18nString,
    ),
    "parentduties_waitlist_notify_text": (
        LazyI18nString.from_gettext(gettext_noop(
            "Hello,\n\n"
            "a spot has become free in “{parent_duty_waitlist_slot}”, which you were waiting for. "
            "If you want to switch, please do so quickly – first come, first served:\n"
            "{parent_duty_url}\n\n"
            "Your current duty stays as it is until you change it:\n"
            "{parent_duty}\n\n"
            "Thank you for lending a hand!\n"
            "{event}"
        )),
        LazyI18nString,
    ),
}

for _key, (_default, _type) in DEFAULTS.items():
    settings_hierarkey.add_default(_key, _default, _type)
