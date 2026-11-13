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
| `WDA_HOST`, `WDA_PORT` | `0.0.0.0`, `8080` | Adresse und Port des Servers (der Healthcheck des Images erwartet 8080) |
| `WDA_LLM_BASE_URL` | `https://api.openai.com/v1` | OpenAI-kompatible Basis-URL (auch B-API) |
| `WDA_LLM_MODEL` | `gpt-6-luna` | Modellname |
| `WDA_LLM_API_KEY` | – | Schlüssel für die KI-Anbindung |

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
- Jede Spalte nennt ihre echte Gesamtzahl (jedes Dokument einmal). Bei mehrwertigen
  Feldern ist das weniger als die Summe der Zeilen.
- `provenance` nennt Abrufzeitpunkt, Zahl der Einzelabfragen, Instanz, die Abfrage selbst,
  die aufgelösten Filter und Gruppen und in `notes` (je Eintrag `code` und `text`) jede
  Besonderheit der Zählung (gekürzte Wertelisten, Summen-Gruppen,
  berechnete Spalten).

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
