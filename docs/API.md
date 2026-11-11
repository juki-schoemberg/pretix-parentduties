# REST API

All endpoints live below the pretix event API:
`/api/v1/organizers/<organizer>/events/<event>/`.
Authenticate like with any pretix API call (team token, OAuth or device).
The plugin must be active for the event, otherwise the endpoints answer `404`.

| Permission (team setting “Parent duties”) | Allows |
| --- | --- |
| View (`event.parentduties:read`) | all `GET` requests |
| View and change (`event.parentduties:write`) | creating, changing and deleting slots, moving signups |

## Categories – `parentduty_categories/`

`GET`, `POST`, `GET <id>/`, `PATCH <id>/`, `PUT <id>/`, `DELETE <id>/`

```json
{"id": 2, "name": {"de": "Kuchen"}, "description": null, "position": 1}
```

- `position` defines the order of the categories in the checkout and backend.
- Deleting a category keeps its slots; they become uncategorized.

## Duty slots – `parentduty_slots/`

`GET`, `POST`, `GET <id>/`, `PATCH <id>/`, `PUT <id>/`, `DELETE <id>/`

```json
{
  "id": 1,
  "name": {"de": "Kuchen Montag", "en": "Cake Monday"},
  "description": {"de": "Bitte bis 13:30 an der Küche abgeben"},
  "category": 2,
  "subevent": null,
  "date": "2027-07-12",
  "time_from": "14:00:00",
  "time_to": "16:00:00",
  "capacity": 5,
  "active": true,
  "position": 0,
  "limit_items": [12],
  "occupied": 3,
  "free": 2,
  "waiting": 1
}
```

- `occupied`, `free` (`null` = unlimited) and `waiting` are read-only.
- Order: by category (`position` of the category, uncategorized last), then by the slot's own
  position. The list endpoint returns slots in this order.
- Filters: `active`, `category`, `date`, `subevent`.
- Changed in 0.5.0: the free-text field `tag` was replaced by `category` (category ID).
- A slot with signups cannot be deleted (`400`); set `"active": false` instead.
- Increasing the capacity or reactivating a slot hands free spots to the waitlist.

## Signups – `parentduty_signups/`

`GET`, `GET <id>/`, `POST <id>/move/`

```json
{
  "id": 7,
  "order": "ABC12",
  "primary": true,
  "kind": "slot",
  "status": "active",
  "slot": 1,
  "covered_by": null,
  "covered_by_order": null,
  "family_code": "ABC12",
  "sibling_code": "",
  "voucher": null,
  "helper_name": "Anna Muster",
  "helper_phone": "0151 23456789",
  "note": "",
  "created": "2026-11-09T10:12:00Z"
}
```

- `kind`: `slot`, `sibling`, `exempt`, `cannot_help`; `status`: `active`, `needs_review`, `canceled`.
- `voucher`: code of the voucher that exempted the order (only on `exempt` signups created that way).
- Filters: `status`, `kind`, `slot`, `primary`, `order` (order code, case-insensitive).
- `POST <id>/move/` with `{"slot": 3}` moves the signup like the backend does
  (capacity may be exceeded deliberately) and returns the updated signup.

## Waitlist – `parentduty_waitlist/`

`GET`, `GET <id>/`

```json
{"id": 4, "slot": 1, "signup": 9, "order": "XYZ34", "status": "waiting",
 "created": "2026-11-09T10:15:00Z", "notified_at": null}
```

- `status`: `waiting`, `fulfilled`, `removed`. Filters: `slot`, `status`, `signup`.

Signups are also included in the order API (`plugin_data.parent_duties`).
