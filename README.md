# WLO-Daten-Dashboard

Quellen- und Daten-Dashboard für WirLernenOnline und andere edu-sharing-Repositorien.
Es ruft Zahlen live ab, lässt Filter und Ansichten zusammenstellen und erzeugt aus
gespeicherten Templates Dashboards und Zahlen-Dokumente.

**Grundsatz:** Nach außen gehen nur Auswertungen. Keine Schnittstelle liefert einzelne
Datensätze aus einem Cache, und alles außer Anmeldung und Health-Check verlangt die
Admin-Anmeldung.

Stand: Paket 3 von 10 (Grundgerüst, edu-sharing-Client, Abfrage-Engine). Design und Paketfolge stehen in
[`docs/superpowers/specs/`](docs/superpowers/specs/), die Umsetzungspläne in
[`docs/superpowers/plans/`](docs/superpowers/plans/).

## Start mit Docker

```bash
cp .env.example .env        # WDA_ADMIN_PASSWORD setzen
docker compose up --build   # http://localhost:8080
```

Für den Aufruf über `http://localhost` muss `WDA_COOKIE_SECURE=false` gesetzt sein,
sonst nimmt der Browser das Anmelde-Cookie nicht an. Hinter HTTPS bleibt es bei `true`.

Läuft die App hinter einem Reverse-Proxy in einem anderen Container, muss
`FORWARDED_ALLOW_IPS` auf dessen Adresse gesetzt werden. Sonst sieht die Anmelde-Sperre
alle Aufrufe unter der Adresse des Proxys, und fünf fremde Fehlversuche sperren für eine
Minute auch die richtige Anmeldung.

## Konfiguration

| Variable | Standard | Bedeutung |
|---|---|---|
| `WDA_ADMIN_PASSWORD` | – (Pflicht) | Passwort des Admin-Zugangs; ohne startet die App nicht. Ein neues Passwort beendet alle bestehenden Anmeldungen |
| `WDA_COOKIE_SECURE` | `true` | Anmelde-Cookie nur über HTTPS senden |
| `WDA_SESSION_MAX_AGE` | `43200` | Gültigkeit einer Anmeldung in Sekunden |
| `WDA_SESSION_SECRET` | automatisch | Signatur-Geheimnis; sonst in `WDA_DATA_DIR/session.secret` |
| `WDA_DATA_DIR` | `/data` | internes Datenverzeichnis, wird nie ausgeliefert |
| `FORWARDED_ALLOW_IPS` | `127.0.0.1` | Adressen vertrauenswürdiger Reverse-Proxys (wertet uvicorn aus) |
| `WDA_INSTANCES` | – | weitere edu-sharing-Instanzen als JSON-Liste |
| `WDA_CACHE_TTL` | `900` | Sekunden, die eine Aggregat-Antwort im Speicher gilt (0 = aus) |
| `WDA_VOCAB_TTL` | `86400` | Sekunden, die Felder und Vokabulare einer Instanz gelten |
| `WDA_UPSTREAM_TIMEOUT` | `90` | Timeout für Abfragen an eine Instanz in Sekunden |
| `WDA_UPSTREAM_CONCURRENCY` | `4` | gleichzeitige Abfragen je Instanz |
| `WDA_RUNS_KEEP` | `500` | gespeicherte Läufe je Definition; ältere werden entfernt |
| `WDA_OERSI_URL` | Suchschnittstelle von oersi.org | Adresse für den OERSI-Abruf in Berichten |
| `WDA_TIMEZONE` | `Europe/Berlin` | Zeitzone, in der die Uhrzeiten der Zeitpläne gelten |
| `WDA_SCHEDULER` | `on` | `off` schaltet den Planer ab; Zeitpläne bleiben dann liegen (z. B. bei einer zweiten Instanz auf derselben Datenbank) |
| `WDA_HOST`, `WDA_PORT` | `0.0.0.0`, `8080` | Adresse und Port des Servers (der Healthcheck des Images erwartet 8080) |
| `WDA_SNAPSHOT_PAGE` | `1000` | Materialien je Seite beim lokalen Abzug (10 bis 2000) |
| `WDA_SNAPSHOT_NIGHTLY` | `03:30` | Uhrzeit (in `WDA_TIMEZONE`), zu der vorhandene Abzüge Änderungen nachziehen; `off` schaltet das ab (ebenso `WDA_SCHEDULER=off`) |
| `WDA_LLM_BASE_URL` | `https://api.openai.com/v1` | OpenAI-kompatible Basis-URL (auch B-API) |
| `WDA_LLM_MODEL` | `gpt-6-luna` | Modellname |
| `WDA_LLM_API_KEY` | – | Schlüssel für die KI-Anbindung |
| `WDA_LLM_EFFORT` | `auto` | Denkaufwand (`auto`, `off`, `low`, `medium`, `high`); die App passt ihn an, wenn das Modell ihn mit Werkzeugen ablehnt |
| `WDA_LLM_SETTINGS_UI` | `off` | `on`: Adresse, Modell und Schlüssel lassen sich auch in der App unter „Einstellungen“ eintragen. Sonst gilt allein die Umgebung, und die App zeigt nur an |

Vorbelegte Instanzen: `wlo-prod` (`https://redaktion.openeduhub.net`) und `wlo-staging`
(`https://repository.staging.openeduhub.net`). Ein Eintrag in `WDA_INSTANCES` mit
derselben `id` überschreibt die Vorbelegung. Felder je Instanz: `id`, `name`, `base_url`,
optional `repository` (`-home-`), `metadataset` (`mds_oeh`), `query` (`ngsearch`),
`search_url`, `username`, `password` (nur mit `https`), `facet_fields` und `prefix_fields`
(einwertige Felder, die als Filter per Wortanfang treffen; Standard: Lizenzschlüssel).

Facetten sind nur auf Feldern erlaubt, die im Metadatenset eine Werteliste haben, sowie
auf den in `facet_fields` freigegebenen Feldern (Standard: Lizenzschlüssel, Quelle,
Bezugsquelle, Sprache). Eine Facette über Titel oder URL wäre eine Liste einzelner
Datensätze und wird abgelehnt.

## Schnittstelle

| Route | Anmeldung | Inhalt |
|---|---|---|
| `GET /api/health` | nein | Status und Version |
| `POST /api/auth/login` | nein | `{"password": "…"}`, setzt das Session-Cookie; nach 5 Fehlversuchen je Minute gesperrt |
| `POST /api/auth/logout` | nein | löscht das Cookie |
| `GET /api/auth/status` | nein | ob angemeldet |
| `GET /api/instances` | ja | Instanzen ohne Zugangsdaten |
| `POST /api/instances`, `PUT`/`DELETE /api/instances/{id}` | ja | Instanz anlegen, ändern, entfernen (Seite „Instanzen“). Zugangsdaten gehen nur hinein; sie liegen in der Datenbank im Datenverzeichnis (Dateirechte 0600). Eine Änderung an einer festen Instanz überschreibt sie, Entfernen stellt sie wieder her. |
| `GET /api/settings` | ja | KI-Basis-URL, Modell, ob ein Schlüssel gesetzt ist |
| `GET /api/instances/{id}/about` | ja | Erreichbarkeit, Version und Gesamtzahl der Instanz |
| `GET /api/instances/{id}/fields` | ja | Felder des Metadatensets mit Anzahl der Vokabularwerte |
| `GET /api/instances/{id}/vocab?property=…&q=…` | ja | Werte eines Feldes (URI, Label, Oberbegriff), durchsuchbar |
| `POST /api/instances/{id}/aggregate` | ja | Gesamtzahl und Facetten für Filter; Werte als Label oder URI |
| `POST /api/query` | ja | Tabelle aus einer Abfrage-Beschreibung: Zeilen, Spalten, Gruppen, Formeln, Anteile |

Beispiel für eine Aggregat-Abfrage (offene Lizenzen in der Sekundarstufe I nach Fach):

```json
{"filters": [{"property": "ccm:educationalcontext", "values": ["Sekundarstufe I"]},
             {"property": "license", "values": ["OPEN", "CC_BY_OPEN"]}],
 "facets": ["ccm:taxonid"]}
```

Die Antwort nennt die Gesamtzahl, je Facette die Werte mit Label und Zählung, wie jeder
Filterwert aufgelöst wurde, den Abrufzeitpunkt und ob sie aus dem Cache stammt. Ein
Filterwert, der zu keinem oder zu mehreren Vokabular-Einträgen passt, bricht die Abfrage
mit Vorschlägen ab. Ein Wert, den das Vokabular nicht kennt (etwa ein Facettenwert ohne
URI), lässt sich als `{"value": "…", "literal": true}` unverändert übergeben. Die Abfragen fordern beim Repositorium nie Datensätze an
(`maxItems=0`). Das gemessene Verhalten der edu-sharing-Suche steht in
[`docs/edu-sharing-api-erkenntnisse.md`](docs/edu-sharing-api-erkenntnisse.md).

## Abfragen

`POST /api/query` ist das Format, in dem Kennzahlen, Dashboards und Templates Zahlen
anfordern. Beispiel: Fächer im Hochschulbereich mit OER-Anteil und der Zeile „ohne Wert".

```json
{"instance": "wlo-prod",
 "filters": [{"property": "ccm:educationalcontext", "values": ["Hochschule"]}],
 "rows": {"property": "ccm:taxonid", "missing": true, "top": 20},
 "series": [
   {"id": "gesamt", "label": "Gesamt"},
   {"id": "oer", "label": "OER",
    "filters": [{"property": "license", "values": ["OPEN", "CC_BY_OPEN"]}]},
   {"id": "anteil", "label": "OER-Anteil in %", "formula": "round(oer / gesamt * 100, 1)"}]}
```

- `rows`: Aufschlüsselung nach einem Feld, wahlweise mit `include` (bestimmte Werte),
  `groups` (benannte Wertegruppen), `top`/`other`, `sort` und `missing` („ohne Wert").
- `series`: Spalten mit eigenen Zusatzfiltern oder einer `formula` über andere Spalten
  (`+ - * /`, Klammern, `round`, `max`, `min`). Alternativ erzeugt `columns` eine Spalte je
  Wert eines Feldes.
- `percent`: Anteile je Spalte oder je Zeile.
- Der Lizenzschlüssel (`ccm:commonlicense_key`) trifft beim Repositorium als Filter per
  Wortanfang (`CC_BY` zählte alle `CC_BY…` mit). Beide Abfrage-Routen lehnen solche Filter ab und
  verweist auf die exakten Wege: den Schlüssel als Zeilen verwenden (mit Gruppen für
  Lizenzkategorien) oder über `license` filtern (`OPEN`, `CC_BY_OPEN`, `CC_BY_RESTRICTED`,
  `COPYRIGHT_OTHERS`).
- `topics` (Wissenslandkarte): statt einer Zahl trägt jede Zelle die Themen aus einem Feld,
  etwa `{"property": "cclom:general_keyword", "top": 10}` zu Zeilen (Fach) und Spalten
  (Bildungsstufe). Je Zelle läuft eine Zählung; höchstens 480 Zellen. `rank: "typical"`
  (Vorgabe) stellt nach vorn, was in der Zelle deutlich häufiger ist als im Gesamtbestand,
  `"frequent"` zählt schlicht. Freie Schlagwörter werden bereinigt: Schreibweisen
  zusammengeführt, Füllwörter („einfach erklärt“), Namen von Zeile und Spalte, Bruchstücke
  und Schlagwortsätze, die ein Kanal an alle seine Materialien hängt, fallen weg
  (`stopwords: false` schaltet das ab, `exclude` nennt weitere Wörter). Ein Hinweis in
  `provenance.notes` sagt, dass bereinigt wurde. Mitgeliefert als Dashboard
  „Wissenslandkarte“ (Reiter unter Qualität).
- Jede Spalte nennt ihre echte Gesamtzahl (jedes Dokument einmal). Bei mehrwertigen
  Feldern ist das weniger als die Summe der Zeilen.
- `provenance` nennt Abrufzeitpunkt, Zahl der Einzelabfragen, Instanz, die Abfrage selbst,
  die aufgelösten Filter und Gruppen und in `notes` (je Eintrag `code` und `text`) jede
  Besonderheit der Zählung (gekürzte Wertelisten, Summen-Gruppen,
  berechnete Spalten).

### Füllgrade (Datenqualität)

`fill` zählt je Feld, wie viele Materialien dort mindestens einen Wert haben:

```json
{"instance": "wlo-prod", "filters": [],
 "rows": {"property": "ccm:replicationsource", "top": 25},
 "fill": {"fields": [{"property": "ccm:taxonid"}, {"property": "ccm:commonlicense_key"}],
          "show": "rate"}}
```

Ohne `rows` gibt es eine Zeile je Feld (mit Wert, ohne Wert, Füllgrad in Prozent), mit
`rows` eine Spalte je Feld (`show`: `rate`, `missing` oder `filled`). „Ohne Wert“ ist immer
berechnet. Was sich nicht verlässlich zählen lässt, bleibt leer und steht mit Grund in den
Hinweisen: Felder mit zu vielen Werten, Suchhilfen (`virtual:*`), nicht freigegebene Felder
und mehrwertige Felder, die das Repositorium nicht als Filter zählt (dann mit Spanne).
Füllgrade von Feldern ohne Werteliste (Titel, Beschreibung) kommen erst mit dem Abgleich
der Datensätze. Mitgeliefert ist dazu das Dashboard „Datenqualität“ (Bibliothek,
„Mitgeliefert“).

### Abdeckung und Datenprobleme

- `rows.match` und `columns.match`: nur Werte, die mit diesem Text beginnen. `ccm:taxonid`
  trägt bei WLO zwei Vokabulare in einem Feld, Schulfächer (`…/vocabs/discipline/`) und
  Hochschulfächer nach Destatis (`…/vocabs/hochschulfaechersystematik/`); so lassen sie sich
  trennen.
- `rows.vocab: "outside"`: nur Werte, die die Werteliste des Feldes nicht kennt (Schmutzwerte,
  andere Schreibweisen). `vocab_check: {"fields": [...]}` zählt das je Feld. Beides nur für
  Felder mit Werteliste, damit nie Werte einzelner Datensätze erscheinen.
- `rows.rollup: "top"`: die oberste Ebene des Vokabularbaums als Zeilen, jede samt allen
  Unterwerten (Fächergruppen der Hochschulsystematik statt 259 einzelner Fächer). Im Explorer
  die Schalter „Vokabular“ und „oberste Ebene“.
- Darstellung `heatmap`: die Tabelle mit eingefärbten Zellen, 0 als rötlich hinterlegte Lücke; für
  Prozent-Spalten dasselbe Schema nach dem Wert selbst (unter 50 rot, darüber blau, je dunkler desto höher). Auch im HTML- und PDF-Export.
- Der Menüpunkt „Qualität“ zeigt die mitgelieferten Dashboards „Abdeckung“ (Fach ×
  Bildungsstufe, Materialart, Lizenz, Quelle), „Füllgrade“ und „Datenprobleme“ als Reiter.
- Suchhilfen der Redaktionsoberfläche (`virtual:*`) stehen nicht in den Feldlisten: Sie
  heißen wie die echten Felder, zählen aber nichts.

### Lückenliste

`gaps` macht aus einer Matrix eine Liste der Felder, die am weitesten unter dem bleiben,
was Zeilen- und Spaltensumme erwarten lassen:

```json
{"instance": "wlo-prod",
 "rows": {"property": "ccm:taxonid", "match": "http://w3id.org/openeduhub/vocabs/discipline/", "vocab": "inside"},
 "columns": {"property": "ccm:educationalcontext", "top": 14},
 "gaps": {"limit": 25, "min_expected": 20, "below": 50}}
```

Je Feld stehen die gezählten Materialien, die Erwartung (Zeilensumme × Spaltensumme ÷
Summe der gezeigten Matrix) und der Anteil am Erwarteten in Prozent; nur Felder unter
`below` Prozent der Erwartung (Standard 50), die kleinsten zuerst. Felder, die sich nicht bestimmen ließen, fehlen.
Felder mit einer Erwartung unter `min_expected` bleiben weg, damit seltene Fächer die
Liste nicht füllen. Eine Lücke kann fachlich begründet sein (Philosophie in der
Primarstufe); die Liste zeigt, wo sich das Nachsehen lohnt. Im Dashboard „Abdeckung“
stehen zwei solche Listen für Schulfächer: gegen die Schulstufen und gegen die Materialarten.

### Quellen

Der Reiter „Quellen“ unter „Qualität“ folgt dem Quellenpanel, zeigt aber nur Zählungen: Bezugsquellen und Crawler nach Größe (wie viele haben nur ein Material), Bindung zwischen Crawler und Bezugsquelle, dünne Metadaten und Offenheit je Bezugsquelle, Kosten, Werbung, Zugang, Datenschutz, Altersfreigabe, Erschließungsstatus, Materialart und Sprache je Bezugsquelle in Prozent, dazu die Vollständigkeit der Quelldatensätze. Dafür gibt es zwei Abfrageformen: `profile` (ein Merkmal je Zeile, je Zeilenwert eine Abfrage) und `sizes` (Größenklassen eines Feldes).

Oben stehen die **Datenströme**: je Quelle und je Bezugsquelle der Bestand, der Zufluss (neu in 30 Tagen und in 12 Monaten) und die Pflege (seit 12 Monaten unberührt), dazu das Alter des Bestands je Quelle als gestapelte Balken. Kein Zufluss und alles unberührt heißt: Der Strom ist versiegt. Dahinter stehen zwei Bausteine der Abfrage: der Filterwert `{"since": "30d"}` (auch `12m`, `5y`) für `virtual:time_after_created` und `virtual:time_after_modified`, und `"hidden": true` an einer Spalte, die nur Formeln speist. Das Repositorium kennt nur „seit“; ein Zeitraum ist deshalb die Differenz zweier Spalten.

### Kontext: Gesamtbestand oder eine Quelle

Die Reiter unter „Qualität“ haben eine Auswahl „Kontext“. Mit einer Quelle (Crawler) oder einer Bezugsquelle zählt dieselbe Auswertung nur diesen Ausschnitt. Technisch nimmt `POST /api/definitions/{id}/run` dafür `scope` (bis zu drei Filter), die vor jede Abfrage treten. Ein Lauf mit Kontext ist eine Ansicht und wird nie gespeichert, damit Verlauf und Zeitreihen beim Gesamtbestand bleiben. Filtert eine Kachel selbst dasselbe Feld, meldet sie das, statt falsch zu zählen; die Tiefenprüfung kennt keinen Kontext.

### Tiefenprüfung aus dem lokalen Abzug

Manches kann ein Repositorium nicht zählen: wie lang Beschreibungen sind, ob ein Titel
fehlt, ob dieselbe Web-Adresse mehrfach vorkommt. Dafür liest die Anwendung auf Wunsch
alle Materialien einer Instanz einmal seitenweise aus (Reiter „Tiefenprüfung“ unter
„Qualität“, oder `POST /api/snapshot/{instanz}/start`) und merkt sich je Material nur
Merkmale:

- Längen von Titel und Beschreibung, Anzahl der Schlagwörter, Fächer, Bildungsstufen,
  Materialarten und Sprachen, ob Autor und Lizenz angegeben sind,
- Zeitpunkt der Erfassung und der letzten Änderung,
- Quelle (Crawler) und Bezugsquelle,
- den Rechnernamen der Web-Adresse und ob sie https nutzt,
- für Adresse und Titel je einen nicht umkehrbaren Schlüssel (HMAC mit einem Geheimnis
  der Installation), um gleiche Werte zu erkennen.

Titel, Beschreibungen, Schlagwörter und Web-Adressen selbst werden nicht gespeichert.
Der Abzug liegt in `WDA_DATA_DIR/abzug/` und ist über keine Schnittstelle abrufbar;
heraus kommen nur Zählungen in der Form aller anderen Abfragen:

```json
{"instance": "wlo-prod", "snapshot": {"analysis": "duenn", "by": "publisher", "top": 40}}
```

`analysis` ist eine von `freitext`, `beschreibung`, `titel`, `schlagwoerter`, `duenn`,
`vollstaendigkeit`, `dubletten`, `alter`, `adressen`, `jahrgang`, `rechner`; `by` gliedert
nach `publisher` (Bezugsquelle) oder `source` (Quelle), `show` wählt Anteile oder Anzahlen.
Das mitgelieferte Dashboard „Tiefenprüfung“ stellt 18 solche Kacheln zusammen.

Ein vollständiger Abzug von WLO (rund 320.000 Materialien) dauert etwa zweieinhalb Stunden (gemessen: rund 36 Materialien je Sekunde) und belegt rund 180 MB. Er
lässt sich anhalten und fortsetzen; „Änderungen nachziehen“ liest danach nur, was sich
seit dem letzten Abzug geändert hat; das geschieht für vorhandene Abzüge auch von selbst
jede Nacht (`WDA_SNAPSHOT_NIGHTLY`). Gelöschte Materialien verschwinden erst mit einem
vollständigen Abzug. Ändern sich Adresse, Repositorium, Metadatenset, Query oder Benutzername einer Instanz, wird ihr
Abzug verworfen; eine Umbenennung lässt ihn stehen. In der Liste der Rechner stehen nur
Rechner mit mindestens fünf Materialien einzeln.

## KI: Assistent und Einstellungen

- **Einstellungen** (Menü): zeigt Basis-URL, Modell und ob ein Schlüssel gesetzt ist, und prüft die Verbindung. Eingestellt wird über die Umgebung (`WDA_LLM_BASE_URL`, `WDA_LLM_MODEL`, `WDA_LLM_API_KEY`, `WDA_LLM_EFFORT`), damit der Schlüssel in der App nirgends eingegeben werden muss. Mit `WDA_LLM_SETTINGS_UI=on` lassen sich die Werte auch in der App eintragen; sie gehen dann denen der Umgebung vor. Ein früher in der App eingetragener Schlüssel bleibt in der Datenbank der App liegen, auch wenn der Schalter wieder aus ist; er wird dann nicht benutzt. Wer ihn loswerden will, löscht ihn einmal mit eingeschaltetem Schalter unter „Einstellungen“. `WDA_LLM_BASE_URL` sollte mit `https://` beginnen, sonst reist der Schlüssel unverschlüsselt.
- **Assistent** (Menü): Fragen in eigenen Worten. Der Assistent schlägt Felder und Werte nach,
  zählt über dieselbe Engine wie alle Auswertungen und zeigt Ansichten und Dashboards als
  Karten („Im Explorer öffnen“, „Als Kennzahl speichern“, „Dashboard anlegen“). Der Knopf
  „Fragen“ an einer Auswertung öffnet ihn mit dieser Auswertung als Bezug.
  Der Knopf „Chat“ im Kopf öffnet dieselbe Unterhaltung als Fenster neben der Ansicht, die
  gerade im Bild ist; sie bleibt beim Seitenwechsel erhalten, bis die Seite neu geladen wird.
- **Was das Modell sieht:** Feldnamen, Wertelisten und Zählungen, also nur, was auch
  exportiert werden darf. Es gibt kein Werkzeug, das Datensätze liefert, und keines, das
  speichert; Vorschläge legt erst der Admin an. Zahlen in den Karten stammen aus der
  Zählung, nicht vom Modell.
- Grenzen je Frage: 16 Runden, 32 Werkzeugaufrufe, 8 Minuten, 30 Nachrichten Verlauf.
- **Vorlagen:** Im Chat lässt sich eine Datei anhängen (PDF, Word `.docx`, Excel `.xlsx`,
  PowerPoint `.pptx`, Text, Markdown, CSV, JSON; bis 8 MB), etwa eine Statistik, die
  nachgebaut werden soll. `POST /api/assistant/document` wandelt sie in Markdown um, damit
  das Sprachmodell den Aufbau sieht: Überschriften, Listen und Tabellen (bei Word aus den
  Formatvorlagen, bei PDF geschätzt aus Schriftgröße und Tabellenlinien, bei Excel eine
  Tabelle je Blatt, bei PowerPoint ein Abschnitt je Folie). Höchstens 60 Seiten und
  32.000 Zeichen; gespeichert und protokolliert wird nichts. PDF und Office-Dateien
  werden in einem eigenen Prozess gelesen (20 Sekunden, höchstens zwei zugleich). Der Text geht einmal mit der nächsten Frage an
  das Sprachmodell und gilt dort als Material, nicht als Anweisung. Gescannte PDFs ohne
  Textebene werden abgewiesen.

## Berichte, Export und die OER-Statistik

- `GET /api/runs/{id}/export?format=json|csv|md|html|pdf|xlsx` liefert einen gespeicherten Lauf
  als Datei. HTML und PDF sind in sich vollständig (Grafiken als SVG, nichts wird
  nachgeladen); CSV ist UTF-8 mit BOM und `;` als Trenner. Jede Tabelle nennt Quelle,
  Abrufzeitpunkt und die Hinweise zur Zählung; fehlende Werte stehen als „–", nie als 0.
  Excel (`xlsx`) trägt ein Blatt „Übersicht“ und ein Blatt je Tabelle, Zahlen als Zahlen.
- In der Oberfläche steht unter jeder Tabelle „CSV herunterladen“ und unter jeder Grafik
  „PNG herunterladen“, auch im Explorer, in Vorschauen und im Chat. Beides entsteht im
  Browser aus dem, was gerade zu sehen ist.
- Templates kennen neben Text, Tabelle und Grafik den Abschnitt `matrix`: Zeilen in Gruppen
  mit Zwischensummen, Spaltenblöcke je Quelle. Eine Quelle ist eine Abfrage an die Instanz
  (`query`), ein Abruf bei OERSI (`oersi`, nur Zählungen) oder ein Handeintrag (`manual`).
- Texte dürfen Platzhalter tragen: `{{run.date}}`, `{{run.instance}}`,
  `{{abschnitt.total.spalte}}`, `{{abschnitt.cell.zeile.spalte}}` (bei einer Matrix heißt
  die Spalte `block.spalte`). Auflösbar sind nur Spalten, die die Tabelle zeigt.
- In Handeinträgen (`manual`) ist der Schlüssel einer Zelle der Wert der Zeile (z. B. die
  URI des Fachs) oder genau die Angabe, mit der die Zeile in der Definition steht; berechnete Spalten entstehen auch dort aus den Formeln,
  wenn sie nicht eingetragen sind. PDF gibt es bis 30.000 Tabellenzellen.
- Für die Oberfläche: `GET /api/runs/{id}/report` liefert das Berichtsmodell als JSON
  (Texte zusätzlich als maskiertes HTML), `POST /api/preview` führt eine ungespeicherte
  Definition aus und legt nichts ab, `POST /api/check` prüft eine Definition und nennt alle
  Fehler mit ihrem Ort. Ein ungespeicherter Lauf hat `id: null`.
- `GET /api/builtin` zeigt mitgelieferte Definitionen (`changed`: die installierte Fassung weicht ab; `install?mode=replace` setzt sie zurück), `POST /api/builtin/oer-statistik/install`
  legt die **OER-Statistik** an: Fächer in zehn Fächergruppen × Bildungsbereiche × Quelle ×
  Lizenzkategorien A bis D. Die Zuordnungen (Fach → Fächergruppe, Bildungsstufe → Bereich,
  Lizenz → Kategorie) stehen in der Definition und sind dort änderbar; erzeugt wird die
  Datei mit `backend/scripts/build_oer_template.py`.

## Entwicklung

```bash
cd backend
python -m venv .venv && . .venv/bin/activate
pip install -r requirements-dev.txt && pip install --no-deps -e .
pytest -q
ruff check .
WDA_ADMIN_PASSWORD=dev WDA_COOKIE_SECURE=false WDA_DATA_DIR=./data \
  python -m wda
```

## Oberfläche

Die Oberfläche (Angular, `frontend/`) ist im Docker-Image enthalten und unter `/` erreichbar.

- **Explorer:** Filter über ein Eingabefeld bauen (erst das Feld, dann Werte mit Vorschlägen aus
  dem Vokabular; Name und URI werden beide gezeigt, eine eingefügte URI wird erkannt),
  aufschlüsseln, als Tabelle oder Grafik ansehen, als Kennzahl speichern. Die Adresse enthält
  die ganze Abfrage und lässt sich als Link weitergeben; Ergebnisse stehen nie darin.
- **Bibliothek:** Dashboards, Templates, Kennzahlen und Filter in Gruppen; suchen, duplizieren,
  verschieben, löschen, importieren, exportieren; mitgelieferte Vorlagen (OER-Statistik)
  installieren. „Bearbeiten“ öffnet die Definition mit Prüfung und Vorschau.
- **Bericht und Läufe:** Eine Definition ansehen zählt frisch und speichert nichts; „Lauf
  speichern“ legt den Stand ab, der sich als JSON, CSV, Markdown, HTML und PDF abrufen lässt.
- **Verlauf und Zeitpläne:** Jeder gespeicherte Lauf ist ein Punkt im Verlauf einer
  Definition. Ein Zeitplan lässt sie von selbst laufen; war die Anwendung zum Termin aus, wird
  der Lauf einmal nachgeholt, nach einem Fehlschlag folgen bis zu drei neue Versuche. Am Lauf
  zeigt „Vergleichen mit“ die Veränderung gegenüber einem früheren Lauf, auch in den Exporten.
  Wurde die Definition oder ein von ihr benutzter Filter geändert, steht das als Hinweis dabei.
  Tage in Zeiträumen und Vergleichen meinen den Kalendertag in `WDA_TIMEZONE`.

Entwicklung ohne Docker:

```
cd frontend && npm ci && npx ng build        # schreibt nach backend/wda/static
cd ../backend && python -m wda               # liefert die Oberfläche mit aus
python scripts/demo_server.py                # oder: Backend mit nachgebautem Bestand, Passwort demo-passwort
cd ../frontend && npx ng test --watch=false && node e2e/smoke.mjs
```
