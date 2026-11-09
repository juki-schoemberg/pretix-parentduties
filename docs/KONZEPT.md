# Konzept: pretix-Plugin „Elterndienst“

Nov 8, 2026 · @Tobi

## Ziel & Grundidee

Jede Familie, die ein Kind zu Schömsala (oder einer anderen JuKi-Veranstaltung) anmeldet, sagt schon im pretix-Checkout verbindlich einen Elterndienst zu – genau einmal pro Familie, egal wie viele Kinder oder Bestellungen.

- **Veranstalter** legen pro Event Dienst-Slots an (z. B. „Aufbau Fr“, „Abbau So“, „Kuchen Mo“ … „Kuchen Fr“), jeder mit eigener Kapazität.
- **Eltern** wählen im Anmelde-Workflow einen freien Slot; volle Slots sind sichtbar, aber nicht wählbar.
- **Familien-Regel:** Hat die Familie schon zugesagt (gleiche E-Mail oder Geschwister-Bestellcode), entfällt die Auswahl.

Name: technisch durchgehend englisch – Plugin „Parent Duties“, Repo `juki-schoemberg/pretix-parentduties` (JuKi-Organisation, neben `pretix-bagnumbers` und `pretix-reportgenerator`), Python-Paket `pretix_parentduties`. In der deutschen Oberfläche heißt es per Übersetzung „Elterndienste“.

**Grundsätze**

- **Im Zweifel eine Option:** Alles, was je nach Veranstaltung anders sein kann, wird eine Einstellung im Admin-Bereich statt fest verdrahtet.
- **Immer aktuelles pretix:** entwickelt und getestet gegen das jeweils neueste pretix-Stable (Stand heute 2026.9.0, Python ab 3.11), CI zusätzlich gegen pretix `master`. Keine Kompatibilitäts-Weichen für alte Versionen.
- **Strikt pro Veranstaltung:** Slots, Zusagen, Familien-Erkennung und Einstellungen gelten immer nur für ein Event, nie eventübergreifend. Aktiviert wird wie jedes pretix-Plugin pro Event; zusätzlich schaltet „Checkout-Schritt live“ die Abfrage erst scharf, wenn alles angelegt ist.
- **Pro Produkt steuerbar:** Jedes Produkt hat einen eigenen Schalter, ob es die Elterndienst-Pflicht auslöst (Spenden und Merch nicht).
- **Alles als Platzhalter:** Jede Eigenschaft einer Zusage ist in E-Mails und anderen pretix-Texten nutzbar.
- **Keine eigenen Exporte:** Listen laufen über `pretix-reportgenerator`; dieses Plugin liefert dort nur seine Felder zu.
- **Dashboard ja:** Belegungs-Widget auf dem Event-Dashboard.

Nicht in v1: Serien-Slots (bewusst – jeder Kuchen-Tag ist ein eigener Slot), Tausch zwischen Eltern, eigene Erinnerungslogik (Erinnerungen laufen über pretix-E-Mail-Regeln mit den Platzhaltern). Siehe Ausbaustufen.

## Begriffe & Datenmodell

Drei eigene Modelle reichen: **Dienst-Slot** (was es gibt), **Zusage** (wer was übernimmt) und **Produkt-Konfiguration** (welches Produkt die Pflicht auslöst). Eine Familien-Tabelle braucht v1 nicht – die Familie ergibt sich innerhalb eines Events aus normalisierter E-Mail plus Verknüpfung der Zusagen.

**`DutySlot`** – ein konkreter Dienst, einmalig, mit Kapazität

| Feld | Typ | Zweck |
| --- | --- | --- |
| event | FK Event | Zuordnung, Scoping wie alle pretix-Objekte |
| subevent | FK SubEvent, optional | nur falls ein Event als Serie läuft |
| name | I18nCharField | „Kuchen Montag“, „Aufbau Freitag“ |
| description | I18nTextField | Details, Treffpunkt, Mitbringen |
| tag | Char, optional | freie Gruppierung („Aufbau“, „Kuchen“) für Filter und Export |
| date, time\_from, time\_to | Date/Time, optional | Anzeige und Sortierung |
| capacity | PositiveInt, leer = unbegrenzt | z. B. 5 Kuchen pro Tag |
| active | Bool | ausblenden ohne zu löschen |
| position | Int | Sortierung im Checkout |
| limit\_items | M2M Item, optional | Slot nur bei bestimmten Produkten anbieten |

**`DutySignup`** – die Zusage einer Bestellung

| Feld | Typ | Zweck |
| --- | --- | --- |
| order | FK Order | Bestellung, zu der die Zusage gehört |
| kind | Choice | `slot` (eigener Dienst), `sibling` (über Geschwister abgedeckt), `exempt` (vom Admin befreit), `cannot_help` („kann nicht“, nur wenn Option aktiv) |
| slot | FK DutySlot, nur bei `slot` | belegt einen Platz |
| covered\_by | FK DutySignup, nur bei `sibling` | zeigt auf die Zusage der Familie |
| email\_normalized | Char, indiziert | Familien-Erkennung im Event |
| helper\_name, helper\_phone | Char, optional | wer konkret kommt (Abfrage per Einstellung) |
| status | Choice | `active`, `needs_review`, `canceled` |
| note | Text | Freitext Eltern / interne Notiz |

Pro Bestellung gibt es eine Pflicht-Zusage. Ist „Mehrere Dienste pro Familie“ aktiv, kommen bis zur eingestellten Höchstzahl weitere Zusagen mit `kind=slot` dazu; die Pflicht gilt als erfüllt, sobald mindestens eine aktive Zusage `slot` oder `exempt` existiert.

**`ItemDutyConfig`** – Schalter am Produkt (Muster wie `ItemNumberConfig` in `pretix-bagnumbers`)

| Feld | Typ | Zweck |
| --- | --- | --- |
| item | OneToOne Item | das Produkt |
| triggers\_duty | Bool | löst dieses Produkt die Elterndienst-Pflicht aus? |

Produkte ohne Eintrag erben den Event-Standard „Neue Produkte lösen Pflicht aus“.

Belegung eines Slots = Anzahl Zusagen mit `kind=slot` und `status=active`.

**Event-Einstellungen** (Settings-Store, Präfix `parentduties_`) – alles pro Event:

| Einstellung | Typ | Standard |
| --- | --- | --- |
| Checkout-Schritt live | Bool | aus |
| Schritt verpflichtend | Bool | an |
| Titel und Einleitungstext des Schritts | mehrsprachiger Text | lockere Vorlage |
| „Wir können leider nicht“-Option anbieten | Bool | aus |
| Begründung bei „kann nicht“ verlangen | Bool | an |
| Geschwister-Fallback per Bestellcode | Bool | an |
| Mehrere Dienste pro Familie erlauben | Bool | aus |
| Höchstzahl Dienste pro Familie | Zahl, nur bei Mehrfach | 2 |
| Name der helfenden Person abfragen | aus / optional / Pflicht | Pflicht |
| Telefonnummer abfragen | aus / optional / Pflicht | optional |
| Verpflichtungs-Checkbox auf der Bestätigungsseite | Bool | an |
| Eltern dürfen Dienst selbst ändern bis | Datum, optional | leer = nur Admin |
| Neue Produkte lösen Pflicht aus | Bool | aus |

Ist das Plugin für ein Event nicht aktiviert, gibt es dort weder Schritt noch Platzhalter noch Report-Felder.

## Admin-Sicht im pretix-Backend

Alles läuft über einen eigenen Menüpunkt „Elterndienste“ in der Event-Navigation plus einen Tab in den Event-Einstellungen.

1. **Slots verwalten:** Liste aller Slots mit Belegungsbalken (z. B. „Kuchen Mo – 3/5“), Anlegen, Bearbeiten, Deaktivieren. Button „Duplizieren“, damit 5× Kuchen in einer Minute stehen.
2. **Produkte festlegen:** an zwei Stellen, die dasselbe Feld schreiben (Details unten).
3. **Zusagen ansehen:** pro Slot die Helferliste (Name, Telefon, Bestellcode, Kind(er) aus den Bestellpositionen); Filter nach Kategorie und Status, Sammelansicht `needs_review` und `cannot_help`.
4. **Manuell eingreifen:** Zusage verschieben, hinzufügen, stornieren, Familie befreien (`exempt`), Geschwister-Verknüpfung bestätigen oder lösen. Kapazität darf der Admin bewusst überschreiten.
5. **Bestellung:** Box auf der Bestelldetailseite zeigt die Zusage(n) und verlinkt in die Verwaltung.
6. **Dashboard-Widget:** „Elterndienste: 34 von 50 Plätzen belegt, 2 in Klärung“, verlinkt auf die Slot-Liste; volle und leere Slots farbig markiert.
7. **Listen und Exporte:** keine eigenen – alle Felder stehen im Report-Generator zur Verfügung (eigener Abschnitt unten).
8. **Einstellungen:** eigener Tab unter Event-Einstellungen mit allen Optionen aus der Tabelle im Datenmodell.

**Wo der Produkt-Schalter sitzt – Empfehlung**

- **Am Produkt selbst:** zusätzlicher Kasten „Elterndienst“ auf der Produkt-Detailseite über das Signal `item_forms` – genau so, wie `pretix-bagnumbers` dort seine Taschennummern-Konfiguration einhängt. Wer ein Produkt anlegt oder bearbeitet, sieht den Schalter direkt.
- **Als Übersicht im Plugin-Tab:** Liste aller Produkte des Events mit Checkbox „löst Pflicht aus“, um alles auf einen Blick zu prüfen und schnell umzustellen.
- Für neue Produkte greift der Event-Standard (Voreinstellung: löst nicht aus), damit Spenden und Merch nie versehentlich die Abfrage auslösen.

Die Alternative über pretix’ eingebaute Produkt-Metadaten wäre ohne Code machbar, ist aber ein Freitextfeld ohne Checkbox und damit fehleranfällig.

Alle Änderungen landen im pretix-Log der Bestellung bzw. des Events, damit nachvollziehbar bleibt, wer was verschoben hat.

## Eltern-Sicht: der Checkout-Schritt

Das Plugin hängt einen eigenen Schritt „Elterndienst“ in den pretix-Checkout, direkt nach den Fragen (dort wird die Kontakt-E-Mail erfasst) und vor der Zahlung. Der Schritt erscheint nur, wenn „Checkout-Schritt live“ an ist und mindestens eine Position im Warenkorb ein Produkt mit „löst Pflicht aus“ ist. Wer nur Spende oder Merch bestellt, sieht ihn nicht; bei gemischtem Warenkorb erscheint er. Add-ons und Bundle-Bestandteile zählen nur, wenn ihr eigenes Produkt den Schalter hat; Varianten erben vom Produkt.

1. **Familie schon bekannt?** Passt die E-Mail zu einer aktiven Zusage, sehen die Eltern nur: „Für eure Familie ist schon ein Elterndienst eingetragen – danke! Für diese Anmeldung müsst ihr nichts weiter tun.“ Kein Slot-Name, damit Fremde mit einer fremden Adresse nichts erfahren.
2. **Sonst Auswahl:** Slots als Radio-Liste, gruppiert nach Tag, mit Zeit, Beschreibung und „noch 2 Plätze frei“; volle Slots ausgegraut. Ist „Mehrere Dienste pro Familie“ aktiv, werden daraus Checkboxen bis zur Höchstzahl.
3. **Alternativen darunter:** „Geschwisterkind ist schon angemeldet“ mit Feld für den Bestellcode; „Wir können leider keinen Dienst übernehmen“ nur, wenn in den Einstellungen aktiviert, Begründung je nach Einstellung.
4. **Wer kommt?** Name und Telefonnummer der helfenden Person, jeweils aus, optional oder Pflicht laut Einstellung.
5. **Bestätigungsseite:** Zusammenfassung des gewählten Dienstes plus optional eine Pflicht-Checkbox „Wir verpflichten uns, den gewählten Dienst zu übernehmen.“
6. **Danach:** Dienst steht in der Bestellbestätigung (Platzhalter `{parent_duty}`) und auf der Bestellseite der Eltern. Alle verfügbaren Platzhalter stehen im Abschnitt „Platzhalter“.

Texte bleiben locker und einladend, kein Behördenton – Titel und Einleitung sind pro Event anpassbar. Laut pretix-Qualitäts-Checkliste muss ein zusätzlicher Checkout-Schritt auch im pretix-Widget getestet werden; das gehört in die Testliste.

## Familien-Erkennung: eine Zusage pro Familie

Primär erkennt das Plugin Familien an der E-Mail-Adresse, als Fallback am Bestellcode des Geschwisterkinds. Keine der beiden Wege blockiert die Anmeldung – im Zweifel landet die Zusage bei den Admins zur Prüfung.

**Weg 1 – E-Mail-Abgleich (automatisch)**

- E-Mail aus dem Kontaktformular des Checkouts (liegt in der Cart-Session, sobald der Fragen-Schritt durch ist) wird normalisiert: Leerzeichen weg, Kleinschreibung. Keine Gmail-Punkt-Tricks, das erzeugt falsche Treffer.
- Gibt es im selben Event eine aktive Zusage mit `kind` `slot` oder `exempt` und dieser Adresse, wird die neue Zusage `sibling` mit `covered_by` auf diese Zusage und sofort `active`.
- Zählen nur Bestellungen, die offen oder bezahlt sind – nicht storniert, nicht abgelaufen.

**Weg 2 – Geschwister-Bestellcode (manuell)**

- Für Fälle mit zwei Elternteilen und zwei Adressen: Eltern geben den Bestellcode der anderen Anmeldung ein (steht in deren Bestätigungsmail).
- Code gefunden und dort gibt es eine aktive Zusage: `sibling`, `active`. Zeigt die andere Zusage selbst auf eine dritte, folgt das Plugin der Kette bis zur Wurzel.
- Code unbekannt oder ohne Zusage: `sibling` mit Status `needs_review`. Checkout geht weiter, Admin klärt.

**Gültigkeitsbereich:** immer nur das eine Event. Schömsala, Mitmachzirkus und Mädchentag zählen getrennt, eine eventübergreifende Familienpflicht gibt es bewusst nicht.

Missbrauchsrisiko ist gering (Bestellcode raten bringt nur einen gesparten Kuchen). Die `needs_review`-Liste im Backend reicht als Kontrolle.

## Lebenszyklus: Storno, Änderungen, Kapazität

Die Zusage folgt der Bestellung: wird die Bestellung ungültig, wird der Platz frei, und betroffene Geschwister-Zusagen gehen zur Prüfung.

| Ereignis | Reaktion des Plugins |
| --- | --- |
| Bestellung angelegt | Auswahl aus `order.meta_info` lesen, Zusage anlegen, Kapazität unter Zeilensperre auf dem Slot final prüfen |
| Slot beim Anlegen doch voll | Zusage trotzdem speichern, Status `needs_review`, Admin-Benachrichtigung (sehr selten, s. u.) |
| Bestellung storniert oder abgelaufen | Zusage `canceled`, Platz frei; hängen `sibling`-Zusagen daran, gehen diese auf `needs_review` („Familie hat keinen Dienst mehr“) |
| Bestellung reaktiviert | Zusage wieder `active`, falls Platz frei, sonst `needs_review` |
| Bestellung geteilt | Zusage bleibt am Original, neue Bestellung erhält `sibling`-Zusage darauf |
| Bestellung geändert, kein betroffenes Produkt mehr | Zusage auf `needs_review`, kein automatisches Löschen |
| Testmodus-Bestellung gelöscht | Zusagen löschen |
| Event kopiert | Slots mitkopieren (ohne Zusagen), Produktbezug über die Item-Zuordnung umhängen, Datum leeren; Produkt-Schalter gehen mit den Produkten mit |
| Daten-Shredder läuft | `helper_name`, `helper_phone`, `note` leeren |

**Kapazität und Gleichzeitigkeit:** Geprüft wird dreimal – beim Anzeigen (volle Slots ausgegraut), beim Absenden der Bestellung (Fehlermeldung „Der Dienst ist gerade voll geworden, bitte wähl einen anderen“, Eltern springen zurück) und beim Anlegen der Zusage unter Sperre. Eine echte Reservierung während des Checkouts spart v1 bewusst ein; bei der Größe einer JuKi-Veranstaltung ist der Restfall Überbuchung um einen Platz verkraftbar und wird markiert.

## Platzhalter in E-Mails und Texten

Jede Eigenschaft der Zusage wird als pretix-Textplatzhalter registriert (`register_text_placeholders`) und steht damit in allen E-Mail-Vorlagen, in „E-Mails senden“, in geplanten E-Mail-Regeln und in anderen konfigurierbaren pretix-Texten zur Verfügung – nur in Events, in denen das Plugin aktiv ist.

| Platzhalter | Kontext | Beispiel |
| --- | --- | --- |
| `{parent_duty}` | Bestellung | „Kuchen Montag, 13.07.2027, 14:00–16:00 Uhr“ bzw. „über eure Anmeldung ABC12 abgedeckt“ |
| `{parent_duty_all}` | Bestellung | alle Dienste der Bestellung, eine Zeile je Dienst (bei Mehrfach-Option) |
| `{parent_duty_name}` | Bestellung | Kuchen Montag |
| `{parent_duty_description}` | Bestellung | Bitte bis 13:30 an der Küche abgeben |
| `{parent_duty_category}` | Bestellung | Kuchen |
| `{parent_duty_date}` | Bestellung | 13.07.2027 |
| `{parent_duty_time_from}`, `{parent_duty_time_to}` | Bestellung | 14:00, 16:00 |
| `{parent_duty_time}` | Bestellung | 14:00–16:00 Uhr |
| `{parent_duty_helper_name}` | Bestellung | Anna Muster |
| `{parent_duty_helper_phone}` | Bestellung | 0151 … |
| `{parent_duty_status}` | Bestellung | zugesagt · über Geschwister abgedeckt · befreit · kann nicht · in Klärung |
| `{parent_duty_family_code}` | Bestellung | ABC12 (Bestellung mit der eigentlichen Zusage) |
| `{parent_duty_note}` | Bestellung | Freitext der Eltern |
| `{parent_duty_free_spots}` | Event | 12 |
| `{parent_duty_open_slots}` | Event | eine Zeile je Slot mit freien Plätzen |

Regeln für die Umsetzung:

- Platzhalter mit Kontext „Bestellung“ funktionieren auch in positionsbezogenen Mails, weil dort die Bestellung immer mitkommt.
- Ohne Zusage oder bei leerem Feld wird ein leerer String gerendert, nie „None“.
- Die Einzel-Platzhalter beziehen sich auf die Pflicht-Zusage; weitere freiwillige Dienste stehen in `{parent_duty_all}`.
- Jeder Platzhalter liefert ein Beispiel für die Mail-Vorschau im Backend; der Erklärtext im Editor ist übersetzt und damit deutsch.
- Dieselben Werte gibt es zusätzlich als Ticket-Layout-Variablen (`layout_text_variables`), damit der Dienst auch auf Ticket oder Badge stehen kann.

Damit lässt sich die Erinnerung ohne Zusatzlogik bauen: eine geplante E-Mail-Regel „7 Tage vor Event“ mit „Denkt an euren Elterndienst: {parent\_duty}“.

## Export über den Report-Generator

Das Plugin baut keine Exporte, sondern steuert seine Felder über das offizielle Erweiterungs-Signal von [`pretix-reportgenerator`](https://github.com/juki-schoemberg/pretix-reportgenerator) (Paket `pretix_custom_reports`, beschrieben in dessen `docs/extending.md`) bei. Damit sind Helferlisten, Filter, Sortierung, geplante Exporte und JSON-Vorlagen sofort da.

**Anbindung**

- Empfänger auf `pretix_custom_reports.registry.signals.register_report_fields`, Rückgabe einer Liste von `ReportField` für die Basen `order` und `orderposition`.
- Keys über `plugin_field_key("pretix_parentduties", …)`, also `plugin.pretix_parentduties.<name>`; `provider` = `pretix_parentduties`.
- Werte per `annotation` als `Subquery` auf `DutySignup` (eine Zusage pro Bestellung, `[:1]`), Alias-Präfix `ppd_`. Kein Zugriff pro Zeile – der Report-Generator lehnt das aus Performance-Gründen ab.
- Weiche Abhängigkeit: `reports.py` wird in `ready()` nur importiert, wenn `pretix_custom_reports` installiert ist. Ohne Report-Generator läuft das Plugin normal, nur ohne Report-Felder.
- Die Feldliste ist statisch, deshalb ist keine eigene Cache-Invalidierung nötig.

| Feld-Key (`plugin.pretix_parentduties.…`) | Datentyp | Filter / Sortierung |
| --- | --- | --- |
| `duty` | STRING | enthält, sortierbar |
| `category` | STRING | exakt |
| `date` | DATE | Bereich, sortierbar |
| `time_from`, `time_to` | TIME | sortierbar |
| `kind` | CHOICE (`slot`, `sibling`, `exempt`, `cannot_help`) | exakt |
| `status` | CHOICE (`active`, `needs_review`, `canceled`) | exakt |
| `helper_name` | STRING | enthält |
| `helper_phone` | PHONE | – |
| `family_code` | STRING | exakt |
| `note` | TEXT | enthält |
| `duty_count` | INTEGER | Bereich (bei Mehrfach-Option) |

**Mitgelieferte Report-Vorlagen** als JSON unter `docs/reports/`, importierbar über die JSON-Funktion des Report-Generators:

- „Helferliste je Tag“ – Basis Bestellung, Filter Art = `slot` und Status = `active`, sortiert nach Datum, Uhrzeit, Dienst.
- „Familien in Klärung“ – Filter Status = `needs_review` oder Art = `cannot_help`.

Die Slot-Belegung inklusive leerer Slots steht nicht im Report, weil Reports auf Bestellungen basieren. Dafür sind Slot-Liste und Dashboard-Widget da.

## pretix-Integration: genutzte Signals und APIs

Alles lässt sich mit offiziellen Plugin-Signals umsetzen, ohne pretix-Core anzufassen. Stand: Entwickler-Doku pretix 2026.10 ([General APIs](https://docs.pretix.eu/dev/development/api/general.html), [Creating a plugin](https://pretix.readthedocs.io/en/latest/development/api/plugins.html), [Quality checklist](https://docs.pretix.eu/en/latest/development/api/quality.html)).

| Signal / API | Verwendung |
| --- | --- |
| `presale.signals.checkout_flow_steps` | eigener Schritt als Subklasse von `pretix.presale.checkoutflow.BaseCheckoutFlowStep` |
| `presale.signals.order_meta_from_request` | Auswahl aus der Session in `order.meta_info` übernehmen |
| `base.signals.validate_order` | Kapazität vor Bestellanlage prüfen, bei Bedarf `OrderError` |
| `presale.signals.checkout_confirm_page_content` | Zusammenfassung auf der Bestätigungsseite |
| `presale.signals.checkout_confirm_messages` | Verpflichtungs-Checkbox (per Einstellung) |
| `base.signals.order_placed` | Zusage(n) anlegen |
| `order_canceled`, `order_expired`, `order_reactivated`, `order_changed`, `order_split`, `order_gracefully_delete` | Lebenszyklus laut Tabelle oben |
| `control.signals.item_forms` | Kasten „Elterndienst“ auf der Produktseite (Muster `pretix-bagnumbers`) |
| `base.signals.item_copy_data` | Produkt-Schalter beim Kopieren eines Produkts mitnehmen |
| `base.signals.event_copy_data` | Slots und Produkt-Schalter beim Event-Kopieren übernehmen (Pflicht laut Checkliste) |
| `presale.signals.order_info`, `control.signals.order_info` | Anzeige auf Bestellseite Eltern bzw. Backend |
| `control.signals.nav_event`, `nav_event_settings` | Menüpunkt „Elterndienste“ und Einstellungs-Tab |
| `control.signals.event_dashboard_widgets` | Belegungs-Widget |
| `base.signals.register_text_placeholders` | alle Platzhalter laut Abschnitt oben |
| `base.signals.layout_text_variables` | dieselben Werte für Ticket- und Badge-Layouts |
| `pretix_custom_reports.registry.signals.register_report_fields` | Report-Felder, nur wenn der Report-Generator installiert ist |
| `register_data_shredders` | personenbezogene Felder löschen |
| `base.signals.register_event_permission_groups` | eigene Berechtigung „Elterndienste verwalten“ |
| `api.signals.order_api_details` + eigenes REST-Viewset | Zusagen und Slots per API, z. B. für Vereins-App oder pretix-MCP |
| Log-Entry-Registry (`logentrytypes.py`) | Protokoll aller Änderungen |

Vorgaben aus der Checkliste, die Claude Code beachten soll: `dispatch_uid` und Settings- sowie Session-Schlüssel mit Plugin-Präfix, nur nötige personenbezogene Daten speichern, `.po`-Dateien beim Installieren kompilieren, Checkout-Schritt mit dem Widget testen.

**Zielversion:** `compatibility = "pretix>=2026.9.0"` und bei jedem pretix-Release mitziehen. Einziger Punkt zum Nachschauen im pretix-Code: wo die Kontakt-E-Mail im Cart-Session-Objekt liegt (`pretix/presale/checkoutflow.py`).

## Repo & Dateistruktur

Repo `juki-schoemberg/pretix-parentduties`. Struktur und Konventionen wie [`pretix-bagnumbers`](https://github.com/juki-schoemberg/pretix-bagnumbers): `pyproject.toml` mit Entry-Point, `setup.cfg`, `pytest.ini`, Release-Workflow nach PyPI, `services.py` für die Logik, `logentrytypes.py`, Autor „JuKi Schömberg e.V.“, Lizenz Apache-2.0, `dispatch_uid` mit Präfix-Konstante.

Anlegen (in Claude Code):

```bash
gh repo create juki-schoemberg/pretix-parentduties --public \
  --description "pretix plugin: parent duty sign-up during checkout (JuKi Schömberg)"
```

Zielstruktur:

```text
pretix-parentduties/
├── .github/workflows/
│   ├── tests.yml          # gegen pretix-Stable und pretix master
│   └── release.yml        # wie bei pretix-bagnumbers
├── pretix_parentduties/
│   ├── __init__.py
│   ├── apps.py            # PluginApp + PretixPluginMeta, category FEATURE
│   ├── models.py          # DutySlot, DutySignup
│   ├── signals.py         # alle Receiver, DUID-Präfix
│   ├── checkoutflow.py    # ParentDutyStep(BaseCheckoutFlowStep)
│   ├── services.py        # Familien-Erkennung, Kapazität, Zusage anlegen
│   ├── forms.py
│   ├── views.py           # Backend: Slots, Zusagen, Einstellungen
│   ├── urls.py
│   ├── placeholders.py    # Mail-Platzhalter + Ticket-Layout-Variablen
│   ├── reports.py         # Felder für pretix-reportgenerator (optional)
│   ├── dashboard.py       # Dashboard-Widget
│   ├── api.py             # Serializer + ViewSets
│   ├── logentrytypes.py
│   ├── migrations/
│   ├── templates/pretix_parentduties/
│   └── locale/de/LC_MESSAGES/django.po
├── tests/
│   ├── conftest.py
│   ├── test_services.py   # Familien-Erkennung, Kapazität
│   ├── test_signals.py    # Lebenszyklus
│   ├── test_checkout.py   # Schritt inkl. Widget
│   ├── test_placeholders.py
│   └── test_reports.py    # nur wenn Report-Generator installiert
├── docs/
│   ├── KONZEPT.md         # dieses Dokument
│   ├── images/
│   └── reports/           # JSON-Vorlagen für den Report-Generator
├── pyproject.toml
├── setup.cfg
├── pytest.ini
├── LICENSE
├── README.md
└── .gitignore
```

Entry-Point in `pyproject.toml`: `[project.entry-points."pretix.plugin"] pretix_parentduties = "pretix_parentduties:PretixPluginMeta"`. Keine Laufzeit-Abhängigkeiten außer pretix; der Report-Generator ist nur eine optionale Extra-Abhängigkeit für die Tests.

## Ausbaustufen & offene Fragen

Für Schömsala reicht die erste Stufe; alles danach ist Komfort.

1. **MVP (Schömsala-tauglich):** Slots anlegen und duplizieren, Checkout-Schritt, Familien-Erkennung über E-Mail und Bestellcode, Lebenszyklus, Backend-Listen, alle Platzhalter, Dashboard-Widget, Report-Felder.
2. **Komfort:** Eltern ändern ihren Dienst selbst bis zur Frist, Report-Vorlagen als JSON, Ticket-Layout-Variablen, REST-API.
3. **Später:** Warteliste für volle Slots, Anbindung an Vereins-App bzw. pretix-MCP.

Offene Fragen vor dem Start in Claude Code:

Keine. Alles, was je nach Veranstaltung anders sein kann, ist als Einstellung gelöst; die Kompatibilität des Report-Generators wird in dessen eigenem Repo geklärt.
