# Umsetzungsstand

Stand: 08.11.2026, Version 0.2.0, getestet gegen pretix 2026.9.0 (Python 3.13).

## Fertig (Ausbaustufe 1 / MVP)

Alles aus Stufe 1 des Konzepts: Slots anlegen und duplizieren, Checkout-Schritt
(auch im Widget getestet), Familien-Erkennung über E-Mail und Bestellcode,
Lebenszyklus, Backend-Listen und Eingriffe, alle Platzhalter, Dashboard-Widget,
Report-Felder. Zusätzlich schon aus Stufe 2: Ticket-Layout-Variablen und die
Zusagen in der REST-API der Bestellung (`plugin_data`).

## Ausbaustufe 2 (Teil)

- **Selbständerung bis Frist:** Einstellung „Eltern dürfen ihren Dienst selbst ändern bis“
  (absolut oder relativ zur Veranstaltung, leer = nur Admins). Bis dahin wechseln Eltern auf
  der Dienst-Seite zu einem anderen Slot mit freien Plätzen und ändern Name/Telefon.
  **Zurückziehen geht nicht** – die Seite bietet nur Slots an, keine Alternativen
  („kann nicht“, Geschwister, später). Änderbar sind eigene Dienste (auch offene) und
  „kann nicht“ (wird dann zu einem Dienst); Geschwister-Zusagen ändert die Familie an der
  Wurzel-Bestellung, Befreiungen bleiben Admin-Sache. Kapazität wird unter Zeilensperre
  geprüft, jede Änderung landet im Bestell-Log.
- **Deeplink:** Platzhalter `{parent_duty_url}` (auch als Ticket-Layout-Variable) führt per
  geheimem Bestell-Link ohne Login direkt auf die Dienst-Seite
  `/<veranstalter>/<event>/order/<code>/<secret>/parentduty/`. Nach der Frist ist sie nur
  noch lesbar. Die Box auf der Bestellseite verlinkt ebenfalls dorthin.
- **Englisch:** Quelltexte sind englisch; neben `de` liegt jetzt ein `en`-Katalog bei.

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

- Rest von Stufe 2: Report-Vorlagen als JSON unter `docs/reports/`, eigenes REST-Viewset für Slots.
- Stufe 3: Warteliste, Vereins-App / pretix-MCP.
- Report-Generator: pinnt jetzt `pretix==2026.9.0` (Branch `ccr-0c449b7c-31iofq`), lokal
  laufen die Report-Tests damit ohne `PRETIX_IGNORE_CONFLICTS` grün. Der CI-Job installiert
  den Report-Generator vom Default-Branch und bleibt nicht-blockierend, bis der Pin dort gemergt ist.
- Screenshots für das README.
