# WLO-Daten-Dashboard

Quellen- und Daten-Dashboard für WirLernenOnline und andere edu-sharing-Repositorien.
Es ruft Zahlen live ab, lässt Filter und Ansichten zusammenstellen und erzeugt aus
gespeicherten Templates Dashboards und Zahlen-Dokumente.

**Grundsatz:** Nach außen gehen nur Auswertungen. Keine Schnittstelle liefert einzelne
Datensätze aus einem Cache, und alles außer Anmeldung und Health-Check verlangt die
Admin-Anmeldung.

Stand: Paket 1 von 10 (Grundgerüst). Design und Paketfolge stehen in
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
| `WDA_LLM_BASE_URL` | `https://api.openai.com/v1` | OpenAI-kompatible Basis-URL (auch B-API) |
| `WDA_LLM_MODEL` | `gpt-6-luna` | Modellname |
| `WDA_LLM_API_KEY` | – | Schlüssel für die KI-Anbindung |

Vorbelegte Instanzen: `wlo-prod` (`https://redaktion.openeduhub.net`) und `wlo-staging`
(`https://repository.staging.openeduhub.net`). Ein Eintrag in `WDA_INSTANCES` mit
derselben `id` überschreibt die Vorbelegung. Felder je Instanz: `id`, `name`, `base_url`,
optional `repository` (`-home-`), `metadataset` (`mds_oeh`), `query` (`ngsearch`),
`search_url`, `username`, `password`.

## Schnittstelle (Paket 1)

| Route | Anmeldung | Inhalt |
|---|---|---|
| `GET /api/health` | nein | Status und Version |
| `POST /api/auth/login` | nein | `{"password": "…"}`, setzt das Session-Cookie; nach 5 Fehlversuchen je Minute gesperrt |
| `POST /api/auth/logout` | nein | löscht das Cookie |
| `GET /api/auth/status` | nein | ob angemeldet |
| `GET /api/instances` | ja | Instanzen ohne Zugangsdaten |
| `GET /api/settings` | ja | KI-Basis-URL, Modell, ob ein Schlüssel gesetzt ist |

## Entwicklung

```bash
cd backend
python -m venv .venv && . .venv/bin/activate
pip install -e ".[dev]"
pytest -q
ruff check .
WDA_ADMIN_PASSWORD=dev WDA_COOKIE_SECURE=false WDA_DATA_DIR=./data \
  python -m wda
```
