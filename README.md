# pretix-parentduties

A plugin for [pretix](https://pretix.eu) that lets families commit to a
**parent duty** (set-up, tear-down, baking a cake …) right in the checkout –
exactly once per family and event, no matter how many children or orders.
In the German interface it is called **„Elterndienste“**.

[![License](https://img.shields.io/github/license/juki-schoemberg/pretix-parentduties)](LICENSE)

The full concept (German) is in [`docs/KONZEPT.md`](docs/KONZEPT.md).

## Features

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
- **Vouchers that exempt from the parent duty:** checkbox “Exempt from the
  parent duty” on pretix' voucher form (single and bulk creation). With
  “Applies to the whole order” (default) the whole order is exempt – e.g. a
  staff pass that can only be bought with a voucher, ordered together with the
  children. Without it, only the product bought with the voucher is exempt.
  Later orders of the family are exempt too if they enter the exempt order's
  code as sibling reference (no automatic e-mail match for these).
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
  `{parent_duty_url}`, `{parent_duty_waitlist}`, `{parent_duty_free_spots}`,
  `{parent_duty_open_slots}`.
- **Self-service change and deeplink:** `{parent_duty_url}` is a secret link
  (no login) to the parents' duty page, also linked from the order page.
  Until a configurable deadline (absolute or relative to the event, e.g.
  “7 days before”) parents can switch to another duty with free spots and
  update helper name / phone. Withdrawing is not possible; after the deadline
  the page is read-only. Siblings change at the family's root order,
  exemptions stay admin decisions.
- **Waitlist for full duties** (setting: off / notify / move automatically).
  Families keep their duty and can additionally wait for a full one – in the
  checkout or later on their duty page. When a spot becomes free (cancellation,
  change, admin move, more capacity), the longest waiting family is notified
  by e-mail or moved automatically; spots freed by a move are passed on in
  turn. Both e-mail texts are editable. Backend list with remove action,
  waiting count in the slot list and dashboard, `{parent_duty_waitlist}`.
- **Reminder e-mail:** one click in the settings creates a ready-made scheduled
  e-mail (pretix “Send out emails” module), e.g. 7 days before the event, for
  all orders with products that trigger the duty – with `{parent_duty}`,
  `{parent_duty_description}` and `{parent_duty_url}`.
- **Report fields** for [pretix-reportgenerator](https://github.com/juki-schoemberg/pretix-reportgenerator)
  (`plugin.pretix_parentduties.*`), only if it is installed, plus ready-made
  report templates in [`docs/reports/`](docs/reports/): “Helferliste je Tag” and
  “Familien in Klärung” (import them via the report generator's JSON import).
- **REST API** for slots, signups and the waitlist, see [`docs/API.md`](docs/API.md);
  signups are also included in the order's `plugin_data`.

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

Source strings are English; German (`de`) and English (`en`) catalogs are
included. Update them with `django-admin makemessages -l de -l en` inside
`pretix_parentduties/`, then `django-admin compilemessages`. The compiled
`.mo` files are committed so `pip install git+…` works without a build step.

## Release process

Releases are published to PyPI via a GitHub Actions workflow
(`.github/workflows/release.yml`) using
[PyPI Trusted Publishing](https://docs.pypi.org/trusted-publishers/) (OIDC,
no API token stored in the repo). To cut a release:

1. Bump the version in `pyproject.toml` **and** `pretix_parentduties/__init__.py`
   and commit.
2. Create and publish a GitHub Release (tag it, e.g. `v0.4.0`). Publishing
   the release triggers the workflow, which compiles the translations, builds
   the sdist/wheel and uploads them to PyPI via the `pypi` environment.

The trusted publisher on PyPI is configured for this exact repository
(`juki-schoemberg/pretix-parentduties`), workflow (`release.yml`), and
environment (`pypi`) -- publishing only succeeds from that combination.

## License

Apache-2.0, © JuKi Schömberg e.V.
