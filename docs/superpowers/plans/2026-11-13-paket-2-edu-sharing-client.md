# Paket 2: edu-sharing-Client – Umsetzungsplan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Zählungen und Facetten aus jeder konfigurierten edu-sharing-Instanz holen, Felder und Vokabulare aus dem Metadatenset bereitstellen und Filterwerte wahlweise als Label oder URI annehmen.

**Architecture:** Ein asynchroner Client je Instanz über einem gemeinsamen `httpx.AsyncClient`. Er fordert grundsätzlich `maxItems=0` an und verwirft `nodes`; damit kann diese Schicht keine Datensätze liefern. Ein TTL-Cache im Speicher hält Aggregat-Antworten; das auf Felder und Werte gekürzte Metadatenset liegt zusätzlich im Datenverzeichnis.

**Tech Stack:** httpx, Pydantic v2, FastAPI; Tests mit `httpx.MockTransport`.

**Spec:** `docs/superpowers/specs/2026-11-13-wlo-daten-dashboard-design.md`, gemessenes API-Verhalten in `docs/edu-sharing-api-erkenntnisse.md`.

## Global Constraints

- Jede Suchabfrage geht mit `maxItems=0&skipCount=0`; `nodes` aus Antworten werden nie weitergereicht oder gespeichert.
- Facetten im Body sind Objekte `{"property": …}`.
- Zwei Kriterien mit derselben Property sind ein Fehler vor dem Absenden (das Repositorium nähme still das letzte).
- Ein nicht auflösbarer Filterwert bricht die Abfrage mit Vorschlägen ab; nichts wird still weggelassen.
- Fehler des Repositoriums erreichen die Oberfläche als deutsche Meldung mit Art und Status, nie mit Stacktrace.
- Facetten nur auf Feldern mit Werteliste oder aus `facet_fields` der Instanz; `facet_limit` höchstens 5000 (ergänzt nach dem Review: sonst wird eine Facette über Titel oder URL zur Datensatzliste).
- Timeouts werden nicht wiederholt.
- Standardwerte: `facetLimit` 2000, Cache-TTL 900 s (`WDA_CACHE_TTL`), Metadatenset-TTL 86.400 s, Timeout 90 s, höchstens 4 gleichzeitige Abfragen je Instanz, 2 Wiederholungen bei 502/503/504 und Verbindungsfehlern.
- Zugangsdaten einer Instanz gehen nur als Basic-Auth an deren eigene Basis-URL.

## Review Focus

1. Repositorium antwortet mit HTML, leerem Body oder JSON ohne `pagination`: verständlicher Fehler, kein 500.
2. Facettenwert, den das Metadatenset nicht kennt (Schmutzwert, leerer Wert): wird mit dem Rohwert als Label geliefert.
3. Label, das in einem Vokabular mehrfach vorkommt: Abbruch mit den Kandidaten, kein Raten.
4. Gleiche Abfrage mehrfach gleichzeitig: genau ein Aufruf beim Repositorium.
5. Metadatenset nicht ladbar, aber gekürzte Kopie im Datenverzeichnis vorhanden: Kopie wird weiterverwendet.

---

### Task 1: Client, Fehler, Cache

**Files:** Create `backend/wda/edusharing/{__init__,errors,client}.py`, `backend/wda/cache.py`; Test `backend/tests/test_es_client.py`, `backend/tests/test_cache.py`, `backend/tests/esfake.py`

**Interfaces – Produces:**

- `EduSharingError(kind: str, message: str, status: int | None)`; `kind` ∈ `rejected`, `unknown_property`, `unknown_facet`, `single_value`, `unavailable`, `timeout`, `bad_response`, `invalid_query`.
- `Criterion(property: str, values: tuple[str, ...])`, `FacetValue(value, count)`, `Facet(property, values, other_count)` mit `truncated`, `Aggregate(total: int, facets: dict[str, Facet], fetched_at: float, cached: bool)`.
- `TTLCache(ttl: float, max_entries: int = 2000, clock=time.time)`: `async get_or_load(key, loader, refresh=False) -> tuple[value, cached: bool]` mit Zusammenlegen gleichzeitiger gleicher Anfragen; Fehler werden nicht gecacht.
- `EduSharingClient(instance, http, cache, *, max_concurrency=4, retries=2, backoff=0.5, sleep=asyncio.sleep)`: `async about() -> dict`, `async aggregate(criteria, facets=(), *, facet_limit=2000, content_type="FILES", refresh=False) -> Aggregate`, `async metadataset() -> dict`.

- [ ] Tests: Anfrage-URL, Query-Parameter und Body exakt; Facetten als Objekte; Zuordnung über `property` bei vertauschter Reihenfolge; `sumOtherDocCount` → `truncated`; `nodes` in der Antwort werden verworfen; doppelte Property und leere Werte → `invalid_query` ohne HTTP-Aufruf; 400 mit den drei Staging-Meldungen → passende Art; 400 mit verborgenem Text → `rejected`; 500/HTML/kaputtes JSON/fehlende `pagination` → `bad_response` bzw. `unavailable`; 503 zweimal, dann 200 → Erfolg nach drei Aufrufen; Timeout → `timeout`; Cache-Treffer, `refresh`, Ablauf, Schlüssel unabhängig von der Reihenfolge der Kriterien und Werte; zehn gleichzeitige gleiche Anfragen → ein Aufruf; Basic-Auth nur bei Zugangsdaten.
- [ ] Fehlschlag sehen; umsetzen; grün; Commit.

### Task 2: Metadatenset, Felder, Vokabular, Auflösung

**Files:** Create `backend/wda/edusharing/vocab.py`; Test `backend/tests/test_vocab.py`

**Interfaces – Produces:**

- `VocabValue(id, caption, parent)`, `Field(property, caption, type, values: tuple[VocabValue, ...])`.
- `UnresolvedValue(property, text, suggestions: list[dict])` (Ausnahme), `Resolution(value: str, label: str, how: str)` mit `how` ∈ `uri`, `label`, `literal`.
- `VocabStore(client, data_dir, ttl=86400, clock=time.time)`: `async fields() -> dict[str, Field]`, `async field(property) -> Field | None`, `async resolve(property, text) -> Resolution`, `async label(property, value) -> str`.
- Kürzen: je Widget-ID ein Eintrag, bevorzugt die Variante mit Werten; von den Werten bleiben `id`, `caption`, `parent`. Datei `data_dir/cache/mds-<instanz-id>.json`.
- Auflösung: exakte ID → `uri`; sonst Label ohne Beachtung von Groß/Klein und Randleerzeichen, eindeutig → `label`; mehrdeutig oder unbekannt → `UnresolvedValue` mit bis zu 8 Vorschlägen (Teilzeichenkette, dann Ähnlichkeit); Feld ohne Werteliste oder unbekanntes Feld → `literal`.

- [ ] Tests mit einem kleinen Metadatenset: doppelte Widget-IDs; Auflösung URI/Label/mehrdeutig/unbekannt/literal; `label` für unbekannten Wert gibt den Wert zurück; zweiter Aufruf lädt nicht neu; neue `VocabStore`-Instanz liest die Datei statt des Netzes; abgelaufene Datei + Netzfehler → Datei wird weiterverwendet; ohne Datei + Netzfehler → `EduSharingError`.
- [ ] Fehlschlag sehen; umsetzen; grün; Commit.

### Task 3: Routen

**Files:** Create `backend/wda/api/edusharing.py`, `backend/wda/services.py`; Modify `backend/wda/main.py`, `backend/wda/config.py`, `backend/pyproject.toml`; Test `backend/tests/test_es_routes.py`

**Interfaces – Produces** (alle hinter `require_admin`):

- `GET /api/instances/{id}/about` → `{"reachable": true, "version": {...}, "total": int}` oder `{"reachable": false, "error": {...}}`.
- `GET /api/instances/{id}/fields` → `[{"property","caption","type","value_count"}]`.
- `GET /api/instances/{id}/vocab?property=…&q=…&limit=50` → `{"property","caption","total","values":[{"id","caption","parent"}]}`.
- `POST /api/instances/{id}/aggregate` mit `{"filters":[{"property","values":[…]}],"facets":[…],"facet_limit":2000,"refresh":false}` → `{"instance","total","facets":[{"property","caption","other_count","truncated","values":[{"value","label","count"}]}],"filters":[{"property","values":[{"value","label","how"}]}],"fetched_at","cached"}`.
- Fehler: unbekannte Instanz 404; `UnresolvedValue` 422 mit `{"detail","property","text","suggestions"}`; `EduSharingError` 502 (`timeout` 504, `invalid_query` 422) mit `{"detail","kind","upstream_status"}`.
- `Services(settings, instances, http)` in `app.state.services`: `client(instance_id)`, `vocab(instance_id)`, `async aclose()`; `create_app(..., transport=None)` reicht einen Test-Transport durch.

- [ ] Tests: alle vier Routen mit Fake-Repositorium; Label-Filter wird als URI gesendet; Facettenwerte tragen Labels, Schmutzwerte den Rohwert; 404/422/502/504; Antworten enthalten nie `nodes`; Leitplanken-Test aus Paket 1 deckt die neuen Routen ab.
- [ ] Fehlschlag sehen; umsetzen; grün; Commit.
