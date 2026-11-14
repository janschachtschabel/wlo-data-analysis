# Paket 4: Store und Verwaltung – Umsetzungsplan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Filter, Kennzahlen, Dashboards und Dokument-Templates dauerhaft speichern, in Gruppen ordnen, duplizieren, als JSON aus- und einführen und als Lauf ausführen. Ein Lauf speichert die Ergebnis-Tabellen (nur Aggregate) mit Zeitstempel; darauf bauen Export (Paket 5) und Historie (Paket 7) auf.

**Architecture:** Eine SQLite-Datei `data_dir/store.sqlite3` (WAL), Zugriff über `sqlite3` der Standardbibliothek in `asyncio.to_thread`. Alle vier Arten liegen in einer Tabelle `definitions` mit einer je Art validierten JSON-Spezifikation. Der `Runner` sammelt die Abfragen einer Definition ein, hängt gespeicherte Filter an, führt sie über die Engine aus Paket 3 aus und legt die Tabellen ab.

**Tech Stack:** wie bisher; keine neue Abhängigkeit (Abweichung vom Design, das SQLAlchemy nannte: Für fünf Tabellen ohne Beziehungslogik ist `sqlite3` kleiner und ebenso gut testbar).

**Spec:** Design Abschnitte 5.4, 5.5, 5.7.

## Datenmodell

```
groups       id TEXT PK, name TEXT, position INTEGER, created_at
definitions  id TEXT PK, kind TEXT, name TEXT, description TEXT, group_id TEXT NULL → groups,
             spec TEXT (JSON), version INTEGER, created_at, updated_at
refs         source → definitions (CASCADE), target → definitions (RESTRICT)   wer verweist auf wen
runs         id TEXT PK, definition_id TEXT NULL (bleibt beim Löschen der Definition erhalten: SET NULL),
             kind, name, definition_version, spec TEXT (JSON, Stand zum Laufzeitpunkt),
             status TEXT (ok | partial | failed), started_at, finished_at
run_results  run_id → runs (CASCADE), key TEXT, title TEXT, query TEXT (JSON, aufgelöst),
             result TEXT (JSON, Ergebnis der Engine) NULL, error TEXT (JSON) NULL, PK (run_id, key)
meta         key TEXT PK, value TEXT          (Schema-Version)
```

IDs sind vom Server vergebene Zufallswerte (`secrets.token_urlsafe(9)`), beim Import darf der Aufrufer sie vorgeben (Muster `[A-Za-z0-9_-]{6,40}`).

## Spezifikationen je Art (`extra="forbid"`)

```
filter     {instance: str?, filters: [Filter]}                    (Filter wie in Paket 3)
metric     {query: QuerySpec, filter_ids: [id] = [], view: View = {type: "table"}}
dashboard  {instance: str?, filter_ids: [id] = [],
            tiles: [{id, title, metric_id: id? | query: QuerySpec?, view: View?,
                     layout: {x, y, w, h}}]}                       höchstens 40 Kacheln
template   {instance: str?, filter_ids: [id] = [], title: str,
            sections: [{id, type: "text", markdown}
                     | {id, type: "table" | "chart", title, metric_id? | query?, view?}]}   höchstens 60 Abschnitte
View       {type: "table"|"number"|"bar"|"stacked_bar"|"line"|"pie", options: {str: Skalar} = {}}
```

`instance` auf Dashboard oder Template überschreibt die Instanz jeder enthaltenen Abfrage (dasselbe Dashboard für Prod und Staging). Platzhalter in `markdown` sind Sache von Paket 5; hier wird der Text nur gespeichert (höchstens 20.000 Zeichen).

## Global Constraints

- Die Datenbank liegt im Datenverzeichnis und wird von keiner Route ausgeliefert. Gespeichert werden nur Definitionen und Ergebnis-Tabellen der Engine, nie Datensätze.
- Jede neue Route hängt hinter `require_admin`; der Leitplanken-Test aus Paket 1 muss sie automatisch erfassen.
- Schreibzugriffe laufen einzeln hintereinander (ein Lock im Prozess), jede Operation in einer Transaktion; `PRAGMA foreign_keys=ON`, `journal_mode=WAL`, `busy_timeout`.
- Jede Spezifikation wird beim Speichern und beim Import vollständig validiert. Verweise (`group_id`, `filter_ids`, `metric_id`) müssen existieren und die richtige Art haben, sonst 422. Formeln in Abfragen werden beim Speichern kompiliert (Fehler → 422), Labels werden erst beim Lauf aufgelöst.
- Löschen einer Definition, auf die andere verweisen, ist 409 mit der Liste der Verwender. Löschen einer Gruppe hängt ihre Definitionen aus (keine Kaskade).
- Ändern verlangt die aktuelle `version` (optimistische Sperre); veraltete Version ist 409.
- Gespeicherte Filter werden beim Lauf vor die Grundfilter der Abfrage gesetzt. Kommt ein Feld dabei doppelt vor (das Repositorium nähme still nur ein Kriterium), ist das ein Fehler dieser Kachel. Ein gespeicherter Filter mit `instance` passt nur zu Abfragen derselben Instanz.
- Ein Lauf führt alle Abfragen einer Definition nebeneinander aus (der Client begrenzt und cacht). Scheitert eine Kachel, trägt ihr Ergebnis den Fehler (`detail`, `kind`), die übrigen laufen weiter: Status `partial`; scheitern alle: `failed`. Der Lauf wird in jedem Fall gespeichert.
- Aufbewahrung: je Definition höchstens `WDA_RUNS_KEEP` Läufe (Standard 500), ältere werden beim Speichern eines neuen entfernt. Läufe ohne Definition bleiben, bis man sie löscht.
- Grenzen: Name 1 bis 200 Zeichen, Beschreibung bis 2.000, Spezifikation bis 256 KB, Import bis 5 MB und 2.000 Einträge.
- Fehler in deutschen Sätzen, Form `{"detail", "kind"}`.

## Aus dem übergreifenden Review der Pakete 1 bis 3 übernommen

- Erledigt vor diesem Paket: Das Ergebnis der Engine trägt `schema_version`, die Abfrage, die Instanz (mit Adresse), aufgelöste Gruppen, stabile Spalten- und Gruppen-IDs und Hinweise mit Code. Läufe speichern es unverändert.
- `refresh` gehört nicht in eine gespeicherte Abfrage: Beim Speichern wird es auf `false` gesetzt; „neu laden" ist eine Eigenschaft des Laufs.
- Jede neue Route kommt in die feste Liste des Leitplanken-Tests.
- Nicht in diesem Paket: Instanzen in der Oberfläche bearbeiten (Paket 6, dann mit Verwerfen von Client und Cache), Zeilen-Hierarchie und Zusammenführen mehrerer Abfragen zu einer Tabelle (Paket 5, Template-Schicht).

## Review Focus

1. Zwei gleichzeitige Änderungen derselben Definition: genau eine gewinnt, die andere bekommt 409; kein verlorenes Update.
2. Import mit Verweisen untereinander in beliebiger Reihenfolge, mit bereits vorhandenen IDs und mit einem kaputten Eintrag: entweder alles oder nichts, klare Meldung mit Position.
3. Lauf, bei dem eine von drei Kacheln am Repositorium scheitert und eine an einem nicht auflösbaren Label: Status `partial`, zwei Fehler mit Meldung, eine Tabelle.
4. Abgestürzter Prozess mitten im Schreiben und Start mit einer Datenbank neuerer Schema-Version: sauberer Start bzw. Abbruch mit Meldung, keine stille Datenänderung.
5. Zyklische oder verwaiste Verweise (Kennzahl gelöscht, während ein Dashboard sie nutzt; Verweis auf eine Definition falscher Art).

---

### Task 1: Datenbank

**Files:** Create `backend/wda/store/__init__.py`, `backend/wda/store/db.py`; Modify `backend/wda/services.py`, `backend/wda/config.py`; Test `backend/tests/test_store_db.py`

- `Database(path)`: `async read(fn)`, `async write(fn)` (fn bekommt eine `sqlite3.Connection`, läuft im Thread, `write` unter Lock und in einer Transaktion), `close()`. Legt das Schema an, prüft `meta.schema_version` (neuer als bekannt → `ConfigError`), Datei-Modus 600 wo möglich.
- `Services.db` (träge angelegt, in `aclose` geschlossen); `Settings.runs_keep: int = 500 (ge 1)`.
- [ ] Tests: Schema entsteht, zweiter Start behält Daten, Fremdschlüssel greifen, Rollback bei Ausnahme, neuere Schema-Version bricht ab, gleichzeitige Schreiber verlieren nichts; grün; Commit.

### Task 2: Definitionen und Gruppen

**Files:** Create `backend/wda/store/specs.py`, `backend/wda/store/definitions.py`; Test `backend/tests/test_definitions.py`

- Pydantic-Modelle der vier Arten; `parse_spec(kind, data) -> BaseModel`; `references(kind, spec) -> set[id]`.
- `DefinitionStore(db)`: `list(kind?, group_id?, q?)` (ohne `spec`, mit `updated_at`, sortiert nach Name), `get(id)`, `create(kind, name, description, group_id, spec, id=None)`, `update(id, version, …)`, `delete(id)`, `duplicate(id)` (Name + „ (Kopie)"), `used_by(id)`; Gruppen: `groups()`, `create_group`, `rename_group`, `move_group`, `delete_group`.
- Fehler: `NotFound`, `Conflict(message, used_by=[…])`, `InvalidDefinition(message)`.
- [ ] Tests für jede Regel aus den Global Constraints, die Definitionen betrifft; Fehlschlag sehen; umsetzen; grün; Commit.

### Task 3: Import und Export

**Files:** Create `backend/wda/store/transfer.py`; Test `backend/tests/test_transfer.py`

- `export(ids: list[id] | None) -> {"format": "wda-definitions", "version": 1, "exported_at", "groups": […], "definitions": […]}`; bei Auswahl kommen die verwiesenen Definitionen und Gruppen mit.
- `import_(data, mode: "skip" | "replace" | "copy")`: validiert alles vor dem ersten Schreiben, sortiert nach Verweisen, eine Transaktion; `copy` vergibt neue IDs und schreibt Verweise um. Antwort: je Eintrag `created | replaced | skipped`.
- [ ] Tests: Rundreise, die drei Modi, Verweise in beliebiger Reihenfolge, kaputter Eintrag ändert nichts, fremdes Format; grün; Commit.

### Task 4: Läufe

**Files:** Create `backend/wda/store/runner.py`; Test `backend/tests/test_runner.py`

- `plan(definition) -> [(key, title, QuerySpec)]` (Kennzahl: ein Eintrag `main`; Dashboard: je Kachel; Template: je Tabellen- und Grafik-Abschnitt), mit Instanz-Überschreibung und angehängten gespeicherten Filtern.
- `async run(definition_id, *, refresh=False, instance=None, save=True) -> Run`; `runs(definition_id?, limit, before)`, `get_run(id)`, `delete_run(id)`; Aufbewahrung.
- [ ] Tests gegen `DataRepo`: Zahlen wie bei direktem `run_query`; Instanz-Überschreibung; Filter-Kollision; Teil-Fehler; Lauf überlebt das Löschen der Definition; Aufbewahrung; `save=False` speichert nicht; grün; Commit.

### Task 5: Routen

**Files:** Create `backend/wda/api/definitions.py`, `backend/wda/api/runs.py`; Modify `backend/wda/main.py`, `README.md`; Test `backend/tests/test_definition_routes.py`

- `GET/POST /api/groups`, `PUT/DELETE /api/groups/{id}`
- `GET/POST /api/definitions` (`kind`, `group`, `q`), `GET/PUT/DELETE /api/definitions/{id}`, `POST /api/definitions/{id}/duplicate`
- `POST /api/definitions/export` (`{"ids": […]?}`), `POST /api/definitions/import?mode=`
- `POST /api/definitions/{id}/run` (`{"refresh", "instance", "save"}`), `GET /api/definitions/{id}/runs`, `GET/DELETE /api/runs/{id}`
- Statuscodes: 404 unbekannt, 409 Konflikt (mit `used_by`), 422 ungültig, 413 zu groß.
- [ ] Tests: jede Route im Erfolgs- und Fehlerfall, Leitplanken-Test erfasst alle neuen Routen, Antworten enthalten keine Schlüssel außerhalb von Definitionen und Engine-Ergebnissen; grün; Commit.
