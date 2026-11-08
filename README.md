# pretix-parentduties

A plugin for [pretix](https://pretix.eu) that lets families commit to a
**parent duty** (set-up, tear-down, baking a cake …) right in the checkout –
exactly once per family and event, no matter how many children or orders.
In the German interface it is called **„Elterndienste“**.

[![License](https://img.shields.io/github/license/juki-schoemberg/pretix-parentduties)](LICENSE)

The full concept (German) is in [`docs/KONZEPT.md`](docs/KONZEPT.md).

## Features (v0.1, MVP)

- **Duty slots per event** with name, description, category, date, time,
  capacity (empty = unlimited), sort position and optional product limit.
  One-click *duplicate*, so five cake days are set up in a minute.
- **Checkout step** after the questions and before payment. Full slots are
  visible but disabled. Optional: several duties per family, “we cannot help”
  (with or without reason), “decide later”, helper name / phone (off,
  optional or required), commitment checkbox on the confirmation page.
  Works in the pretix widget as well.
- **Per product switch** “triggers the parent duty”, both on the product page
  and as an overview in the plugin. Default for new products: off.
- **One duty per family:** automatic match by normalized e-mail address,
  fallback via the order code of a sibling (followed to the root). Unknown
  codes never block the checkout – they land in the review list.
- **Lifecycle:** cancel / expire frees the spot and sends dependent sibling
  signups to review; reactivation checks capacity; split orders are covered by
  the original; changed orders without triggering product go to review; test
  mode deletions clean up; event copy takes slots (without signups, dates
  cleared) and product switches along; data shredder clears helper data.
- **Capacity check** on display, on order submission (`validate_order`,
  parents are sent back to the step) and on signup creation under a row lock.
  The rare overbooking is saved as *needs review* and triggers a pretix
  notification.
- **Backend:** slot list with occupancy bars, signup list with filters
  (duty, category, status, type, order, “under review / cannot help”), move /
  exempt / confirm / cancel / link to family / unlink, manual add, order
  detail box, dashboard widget, settings tab, own permission
  “Parent duties” (view / change). All changes are logged.
- **Placeholders** for e-mails and all other pretix texts, plus the same
  values as ticket/badge layout variables:
  `{parent_duty}`, `{parent_duty_all}`, `{parent_duty_name}`,
  `{parent_duty_description}`, `{parent_duty_category}`, `{parent_duty_date}`,
  `{parent_duty_time_from}`, `{parent_duty_time_to}`, `{parent_duty_time}`,
  `{parent_duty_helper_name}`, `{parent_duty_helper_phone}`,
  `{parent_duty_status}`, `{parent_duty_family_code}`, `{parent_duty_note}`,
  `{parent_duty_free_spots}`, `{parent_duty_open_slots}`.
- **Report fields** for [pretix-reportgenerator](https://github.com/juki-schoemberg/pretix-reportgenerator)
  (`plugin.pretix_parentduties.*`), only if it is installed.
- **REST API:** signups are included in the order's `plugin_data`.

## Installation

```bash
pip install pretix-parentduties
python -m pretix migrate
python -m pretix rebuild
```

Then enable “Parent Duties” in the event's plugin settings, create duty slots,
choose the products that trigger the duty and finally switch on
**Settings → Parent duties → Checkout step live**.

## Development

```bash
pip install pretix -e ".[test]"
cd pretix_parentduties && django-admin compilemessages && cd ..
pytest
```

Tests run against the latest pretix stable and (allowed to fail) pretix
`master`, see `.github/workflows/tests.yml`.

Update translations with `django-admin makemessages -l de` inside
`pretix_parentduties/`, then `django-admin compilemessages`. The compiled
`.mo` files are committed so `pip install git+…` works without a build step.

## License

Apache-2.0, © JuKi Schömberg e.V.
