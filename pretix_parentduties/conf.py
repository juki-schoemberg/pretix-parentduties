"""Event settings of the plugin (settings store, prefix ``parentduties_``)."""
from django.utils.translation import gettext_noop
from i18nfield.strings import LazyI18nString

from pretix.base.reldate import RelativeDateWrapper
from pretix.base.settings import settings_hierarkey

ASK_OFF = "off"
ASK_OPTIONAL = "optional"
ASK_REQUIRED = "required"

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
}

for _key, (_default, _type) in DEFAULTS.items():
    settings_hierarkey.add_default(_key, _default, _type)
