# Umsetzungsstand

Stand: 08.11.2026, Version 0.1.0, getestet gegen pretix 2026.9.0 (Python 3.13).

## Fertig (Ausbaustufe 1 / MVP)

Alles aus Stufe 1 des Konzepts: Slots anlegen und duplizieren, Checkout-Schritt
(auch im Widget getestet), Familien-Erkennung über E-Mail und Bestellcode,
Lebenszyklus, Backend-Listen und Eingriffe, alle Platzhalter, Dashboard-Widget,
Report-Felder. Zusätzlich schon aus Stufe 2: Ticket-Layout-Variablen und die
Zusagen in der REST-API der Bestellung (`plugin_data`).

## Bewusste Abweichungen vom Konzept

| Thema | Konzept | Umsetzung | Grund |
| --- | --- | --- | --- |
| Verpflichtungs-Checkbox | `checkout_confirm_messages` | eigene Checkbox im Kasten auf der Bestätigungsseite (`checkout_confirm_page_content`), geprüft in `validate_order` | `checkout_confirm_messages` bekommt keinen Request und würde die Checkbox in jedem Warenkorb zeigen – auch bei reiner Spende oder bekannter Familie. |
| `DutySignup` | Felder laut Tabelle | zusätzlich `event`, `primary`, `sibling_code`, `created` | `event` für Scoping und Indizes, `primary` markiert die Pflicht-Zusage bei Mehrfach-Diensten, `sibling_code` hält den eingegebenen Code für die Klärung fest. |
| Bestellungen ohne Checkout (Backend, API, Import) | nicht beschrieben | E-Mail-Treffer → `sibling`; sonst offene Zusage (`slot` ohne Slot, `needs_review`) | Pflicht gilt pro Bestellung; der Admin teilt in der Klärungsliste zu. |
| „Schritt verpflichtend“ aus | nicht beschrieben | zusätzliche Option „Später entscheiden“, es wird keine Zusage angelegt | |
| Report-Feld `duty` | STRING | I18N | Slot-Namen sind mehrsprachig; der Report-Generator behandelt I18N wie Produktnamen. |
| Berechtigung | „Elterndienste verwalten“ | Gruppe `event.parentduties` mit „Ansehen“ / „Ansehen und ändern“ | neues pretix-Berechtigungssystem (`PermissionGroup`). Einstellungen brauchen weiterhin `event.settings.general:write`. |
| Admin-Benachrichtigung bei Überbuchung | – | pretix-Benachrichtigungstyp „Elterndienst überbucht“ | abonnierbar wie alle pretix-Benachrichtigungen. |

Kontakt-E-Mail im Checkout: `cart_session["email"]` (gesetzt im Fragen-Schritt,
`pretix/presale/checkoutflow.py`, `QuestionsStep.post`). Die Auswahl liegt in
der Cart-Session unter `pretix_parentduties` und landet über
`order_meta_from_request` in `order.meta_info["pretix_parentduties"]`.

## Offen

- Stufe 2: Eltern ändern ihren Dienst selbst bis zur Frist (Einstellung noch nicht angelegt),
  Report-Vorlagen als JSON unter `docs/reports/`, eigenes REST-Viewset für Slots.
- Stufe 3: Warteliste, Vereins-App / pretix-MCP.
- Report-Generator: lokal nicht installierbar (er pinnt `pretix==2026.6.0`), die
  Report-Tests laufen in CI, sobald er sich installieren lässt, sonst werden sie übersprungen.
- Screenshots für das README.
