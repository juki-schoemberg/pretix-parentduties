# Umsetzungsstand

Stand: 09.11.2026, Version 0.3.0, getestet gegen pretix 2026.9.0 (Python 3.13).

## Fertig (Ausbaustufe 1 / MVP)

Alles aus Stufe 1 des Konzepts: Slots anlegen und duplizieren, Checkout-Schritt
(auch im Widget getestet), Familien-Erkennung über E-Mail und Bestellcode,
Lebenszyklus, Backend-Listen und Eingriffe, alle Platzhalter, Dashboard-Widget,
Report-Felder. Zusätzlich schon aus Stufe 2: Ticket-Layout-Variablen und die
Zusagen in der REST-API der Bestellung (`plugin_data`).

## Ausbaustufe 2

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
- **Report-Vorlagen** unter `docs/reports/`: „Helferliste je Tag“ (Art = Dienst, Status = aktiv,
  sortiert nach Datum, Uhrzeit, Dienst) und „Familien in Klärung“ (Status = in Klärung oder
  Art = kann nicht). Import über die JSON-Funktion des Report-Generators; ein Test kompiliert und
  führt beide gegen den echten Report-Generator aus (CI-Job „with pretix-reportgenerator“).
- **REST-API** für Slots (CRUD, mit Belegung), Zusagen (lesen, filtern, verschieben) und
  Warteliste (lesen), siehe `docs/API.md`.
- **Erinnerungs-Mail:** Button in den Einstellungen legt eine geplante E-Mail im pretix-Modul
  „E-Mails senden“ an (Standard 7 Tage vor Beginn, 10:00 Uhr, nur an Bestellungen mit Produkten,
  die die Pflicht auslösen) und öffnet sie im Regel-Editor. Das Modul wird bei Bedarf aktiviert.
  Hinweis: Auch Bestellungen mit „kann nicht“ oder „später entscheiden“ bekommen die Mail; dort
  steht dann „kein Dienst“ bzw. nichts – pretix-Regeln können nicht nach Plugin-Daten filtern.

## Ausbaustufe 3: Warteliste

- Einstellung „Warteliste für volle Dienste“: aus (Standard) / per E-Mail benachrichtigen /
  automatisch verschieben. „Benachrichtigen“ verlangt eine Änderungsfrist, weil Eltern dann
  selbst wechseln. Die Warteliste läuft bis zur Änderungsfrist (falls gesetzt) und bis zum
  Beginn der Veranstaltung.
- Familien behalten immer ihren Dienst und warten zusätzlich auf einen vollen – im Checkout
  (Häkchen am vollen Slot, ein freier Dienst bleibt Pflicht) oder später auf der Dienst-Seite.
- Wird ein Platz frei (Storno, Ablauf, Änderung durch Eltern oder Admin, Bestellung ohne
  auslösendes Produkt, Testmodus-Löschung, mehr Kapazität, Slot reaktiviert), kommt die am
  längsten wartende Familie dran. „Automatisch“: Dienst wird verschoben, der alte Platz geht
  an die nächste Warteliste weiter (Kette). „Benachrichtigen“: je freiem Platz eine Mail an die
  nächste noch nicht benachrichtigte Familie; wer wechselt, hat den Platz.
- Erreicht eine Familie ihren Wunsch-Dienst (auf welchem Weg auch immer), sind ihre übrigen
  Wartelisten-Einträge erledigt. Stornierte Zusagen verlassen die Warteliste.
- Beide E-Mail-Texte sind pro Veranstaltung anpassbar (Platzhalter wie in allen Mails plus
  `{parent_duty_waitlist_slot}`), Versand in der Sprache der Bestellung, alles im Bestell-Log.
- Backend: eigener Menüpunkt „Warteliste“ mit Filter und Entfernen, Spalte in der Slot-Liste,
  Zähler im Dashboard-Widget, Platzhalter `{parent_duty_waitlist}`.

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

- Anbindung an Vereins-App / pretix-MCP (bewusst nicht umgesetzt).
- Report-Generator pinnt `pretix==2026.6.0`; der CI-Job läuft deshalb mit
  `PRETIX_IGNORE_CONFLICTS=True` und blockiert nicht. Lokal ist er hier nicht installiert.
- Screenshots für das README.
