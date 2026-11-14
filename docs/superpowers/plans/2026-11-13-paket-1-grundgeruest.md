# Paket 1: Grundgerüst – Umsetzungsplan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ein startfähiger, abgesicherter Container mit Konfiguration, Instanz-Verzeichnis, Admin-Login und Health-Route, auf dem alle weiteren Pakete aufsetzen.

**Architecture:** FastAPI-Anwendung als Paket `wda` unter `backend/`. Eine App-Fabrik `create_app(settings)` hängt alle Router unter `/api` ein; alles außer Health und Login verlangt die Admin-Session. Die SPA kommt in Paket 6, bis dahin liefert `/` eine statische Platzhalter-Seite.

**Tech Stack:** Python 3.12+, FastAPI, Pydantic v2, pydantic-settings, itsdangerous, uvicorn, pytest, httpx (Testclient), ruff, Docker.

**Spec:** `docs/superpowers/specs/2026-11-13-wlo-daten-dashboard-design.md`

## Global Constraints

- Umgebungsvariablen tragen das Präfix `WDA_`.
- Ohne `WDA_ADMIN_PASSWORD` startet die App nicht (Abbruch mit klarer Meldung).
- Jede Route unter `/api` außer `/api/health`, `/api/auth/login`, `/api/auth/status`, `/api/auth/logout` antwortet ohne Session mit 401.
- Keine Route gibt Zugangsdaten einer Instanz, den LLM-Schlüssel oder das Admin-Passwort aus.
- Vorbelegte Instanzen: `wlo-prod` = `https://redaktion.openeduhub.net`, `wlo-staging` = `https://repository.staging.openeduhub.net`; Repository `-home-`, Metadatenset `mds_oeh`, Abfrage `ngsearch`.
- LLM-Standard: Basis-URL `https://api.openai.com/v1`, Modell `gpt-6-luna`.
- Datenverzeichnis `WDA_DATA_DIR` (Standard `/data`) wird nie als statischer Inhalt ausgeliefert.
- Container läuft als Nicht-Root-Benutzer.

## Review Focus

1. Passwort mit Sonderzeichen/Umlauten oder sehr lang: Login funktioniert, Vergleich in konstanter Zeit.
2. Manipuliertes oder abgelaufenes Session-Cookie: 401, kein 500.
3. `WDA_INSTANCES` mit kaputtem JSON, doppelter ID oder URL ohne `https`/`http`: Start bricht mit verständlicher Meldung ab.
4. Viele falsche Logins: nach 5 Fehlversuchen je Minute und Adresse 429.
5. Pfade wie `/../data/x` oder `/data/x`: liefern nie Dateien aus dem Datenverzeichnis.

---

## Dateistruktur

```
backend/
  pyproject.toml            Abhängigkeiten, ruff, pytest
  wda/
    __init__.py
    config.py               Settings (Umgebung), Start-Validierung
    instances.py            Instance-Modell, Vorbelegung, Registry
    security.py             Session-Token, Passwortvergleich, Login-Drossel
    deps.py                 require_admin
    main.py                 create_app, Router, Platzhalter-Seite
    api/
      __init__.py
      health.py             GET /api/health
      auth.py               login / logout / status
      instances.py          GET /api/instances
      settings.py           GET /api/settings (LLM-Basis-URL, Modell, ob Schlüssel gesetzt)
    static/index.html       Platzhalter
  tests/
    conftest.py             settings- und client-Fixtures
    test_config.py  test_instances.py  test_auth.py  test_routes_guard.py  test_static.py
Dockerfile  docker-compose.yml  .dockerignore  .env.example  .gitignore
.github/workflows/ci.yml
README.md
```

### Task 1: Konfiguration und Instanzen

**Files:** Create `backend/pyproject.toml`, `backend/wda/config.py`, `backend/wda/instances.py`; Test `backend/tests/test_config.py`, `backend/tests/test_instances.py`

**Interfaces – Produces:**

- `class Settings(BaseSettings)`: `admin_password: SecretStr`, `session_secret: SecretStr | None`, `session_max_age: int = 43200`, `cookie_secure: bool = True`, `data_dir: Path = Path("/data")`, `instances: str | None` (JSON-Liste), `llm_base_url: str = "https://api.openai.com/v1"`, `llm_api_key: SecretStr | None`, `llm_model: str = "gpt-6-luna"`.
- `load_settings() -> Settings` wirft `ConfigError` mit deutscher Meldung, wenn das Passwort fehlt oder leer ist.
- `class Instance(BaseModel)`: `id` (Slug `[a-z0-9-]+`), `name`, `base_url` (http/https, ohne Schrägstrich am Ende), `repository="-home-"`, `metadataset="mds_oeh"`, `query="ngsearch"`, `search_url: str | None`, `username: str | None`, `password: SecretStr | None`; Methode `public() -> dict` ohne Zugangsdaten, mit `has_credentials: bool`; Property `rest_url` = `base_url + "/edu-sharing/rest"`.
- `class InstanceRegistry`: `__init__(extra_json: str | None)`, `list() -> list[Instance]`, `get(id) -> Instance` (wirft `KeyError`). Zusätzliche Instanzen mit gleicher ID wie eine vorbelegte überschreiben diese; doppelte IDs innerhalb der Zusatzliste sind ein `ConfigError`.

- [ ] Tests schreiben: Standardwerte; fehlendes Passwort → `ConfigError`; Vorbelegung enthält `wlo-prod` und `wlo-staging` mit den URLs oben; `rest_url`; `public()` enthält weder `password` noch `username`; kaputtes JSON, doppelte ID, `ftp://`-URL → `ConfigError`; Überschreiben von `wlo-prod`.
- [ ] Tests laufen lassen, Fehlschlag sehen; umsetzen; Tests grün; Commit.

### Task 2: Sicherheit und Login

**Files:** Create `backend/wda/security.py`, `backend/wda/deps.py`, `backend/wda/api/auth.py`, `backend/wda/api/health.py`, `backend/wda/main.py`; Test `backend/tests/conftest.py`, `backend/tests/test_auth.py`

**Interfaces – Produces:**

- `SessionSigner(secret: str, max_age: int)`: `issue() -> str`, `valid(token: str | None) -> bool` (itsdangerous `URLSafeTimedSerializer`).
- `resolve_session_secret(settings) -> str`: nimmt `WDA_SESSION_SECRET`, sonst eine in `data_dir/session.secret` abgelegte Zufallszahl (Datei-Modus 600), sonst (Verzeichnis nicht schreibbar) eine Zufallszahl im Speicher.
- `verify_password(given: str, expected: str) -> bool` mit `hmac.compare_digest` auf UTF-8-Bytes.
- `LoginThrottle(limit=5, window=60)`: `allow(key: str) -> bool`, `reset(key)`; zählt nur Fehlversuche.
- `require_admin(request)`: 401 mit `{"detail": "Nicht angemeldet"}` ohne gültiges Cookie `wda_session`.
- `create_app(settings: Settings | None = None) -> FastAPI`; legt `app.state.settings`, `.signer`, `.throttle`, `.instances` an. OpenAPI/Docs-Routen sind abgeschaltet.
- Routen: `GET /api/health → {"status":"ok","version":…}`; `POST /api/auth/login {"password"}` → 204 + Cookie (httpOnly, SameSite=Strict, Secure nach `cookie_secure`, Pfad `/`), 401 bei falschem Passwort, 429 bei Drossel; `POST /api/auth/logout` → 204, Cookie gelöscht; `GET /api/auth/status → {"authenticated": bool}`.

- [ ] Tests schreiben: Login richtig/falsch; Passwort `"pässwört ✓ " + "x"*500`; Status vor/nach Login und nach Logout; manipuliertes Cookie → Status false; abgelaufenes Token (max_age=-1) ungültig; sechster Fehlversuch → 429, richtiger Login danach weiterhin 429 bis Fenster abläuft (Zeitquelle injizierbar); Cookie-Attribute.
- [ ] Fehlschlag sehen; umsetzen; grün; Commit.

### Task 3: Geschützte Routen, Platzhalter-Seite, Leitplanken-Test

**Files:** Create `backend/wda/api/instances.py`, `backend/wda/api/settings.py`, `backend/wda/static/index.html`; Modify `backend/wda/main.py`; Test `backend/tests/test_routes_guard.py`, `backend/tests/test_static.py`

**Interfaces – Produces:**

- `GET /api/instances → [Instance.public()]`, `GET /api/settings → {"llm": {"base_url", "model", "api_key_set"}, "version"}`; beide über `require_admin`.
- `PUBLIC_API_PATHS: frozenset[str]` in `main.py` mit genau den vier öffentlichen Pfaden.
- `/` liefert die Platzhalter-Seite; unbekannte Pfade außerhalb `/api` liefern ebenfalls die Seite (SPA-Fallback), unbekannte `/api/*`-Pfade 404 als JSON.

- [ ] Leitplanken-Test: iteriert über `app.routes`, ruft jede `/api`-Route außerhalb `PUBLIC_API_PATHS` mit jeder ihrer Methoden ohne Cookie auf und erwartet 401. Der Test schlägt künftig automatisch an, wenn jemand eine ungeschützte Route ergänzt.
- [ ] Tests: `/api/instances` und `/api/settings` enthalten nirgends die Zeichenketten der gesetzten Geheimnisse (Instanz-Passwort, LLM-Schlüssel, Admin-Passwort); eine im Datenverzeichnis angelegte Datei ist über `/data/x`, `/%2e%2e/data/x` und `/static/../x` nicht abrufbar (Antwort ist die Platzhalter-Seite oder 404, nie der Dateiinhalt).
- [ ] Fehlschlag sehen; umsetzen; grün; Commit.

### Task 4: Docker, CI, README

**Files:** Create `Dockerfile`, `docker-compose.yml`, `.dockerignore`, `.env.example`, `.gitignore`, `.github/workflows/ci.yml`; Modify `README.md`

- `Dockerfile`: `python:3.12-slim`, installiert `backend/` ohne Entwicklungs-Abhängigkeiten, Benutzer `wda` (UID 10001), `VOLUME /data`, `EXPOSE 8080`, `HEALTHCHECK` gegen `/api/health`, Start `uvicorn wda.main:create_app --factory --host 0.0.0.0 --port 8080`.
- `docker-compose.yml`: ein Dienst `app`, Port `8080:8080`, `env_file: .env`, benanntes Volume `wda-data:/data`.
- CI: bei Push und Pull Request `ruff check backend` und `pytest` unter Python 3.12 sowie `docker build .`.
- README (deutsch): Zweck, Start mit Docker, Umgebungsvariablen als Tabelle, Entwicklung und Tests.

- [ ] Dateien anlegen; `ruff check backend` und `pytest` lokal grün; wenn ein Docker-Daemon verfügbar ist: Image bauen, Container starten, `/api/health` abfragen, Start ohne Passwort bricht ab; Commit.
