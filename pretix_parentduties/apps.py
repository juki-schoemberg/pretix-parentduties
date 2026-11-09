from django.utils.translation import gettext_lazy as _

from pretix.base.plugins import PluginConfig

from . import __version__


class PluginApp(PluginConfig):
    default = True
    name = "pretix_parentduties"
    verbose_name = _("Parent Duties")

    class PretixPluginMeta:
        name = _("Parent Duties")
        author = "JuKi Schömberg e.V."
        description = _(
            "Families commit to a parent duty (e.g. set-up, tear-down, baking a cake) "
            "right in the checkout – exactly once per family and event."
        )
        visible = True
        version = __version__
        category = "FEATURE"
        compatibility = "pretix>=2026.9.0"

    def ready(self):
        from . import signals  # noqa: F401
        from . import logentrytypes  # noqa: F401
        from . import placeholders  # noqa: F401

        try:
            import pretix_custom_reports  # noqa: F401
        except ImportError:
            pass
        else:
            from . import reports  # noqa: F401
