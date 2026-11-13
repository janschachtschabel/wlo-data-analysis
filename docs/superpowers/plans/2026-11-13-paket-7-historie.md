# Paket 7: Historie – Umsetzungsplan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Kennzahlen, Dashboards und Templates laufen nach Zeitplan von selbst. Aus den gespeicherten Läufen entstehen Zeitreihen („wie hat sich die Zahl entwickelt?“) und Vergleiche („was hat sich seit dem letzten Lauf oder seit einem Datum geändert?“). Beides liest nur gespeicherte Zählungen und ist auch bei Hunderten Läufen schnell.

**Architecture:** Jeder gespeicherte Lauf schreibt seine Zahlen zusätzlich als schmale Zeilen in eine Tabelle `points` (Lauf, Block, Zeile, Spalte, Wert). Zeitreihen und Vergleiche lesen nur diese Tabelle und die Kopfzeilen der Läufe, nie die großen Ergebnis-JSONs. Ein Planer läuft als Aufgabe im selben Prozess (ein Container, ein Prozess) und führt fällige Zeitpläne nacheinander aus. Läufe können als Auftrag gestartet werden (`wait=false`), ihr Fortschritt steht im Speicher des Prozesses.

**Tech Stack:** wie bisher (FastAPI, sqlite3, Angular, ECharts mit Zeitachse). Zeitzonen über `zoneinfo`, Abhängigkeit `tzdata` (Windows, schlankes Image).

**Spec:** Design Abschnitte 5.4 (Läufe), 5.5 (Historie), 5.7.

## Datenmodell (Schema-Version 2)

```sql
CREATE TABLE points (
    run_id TEXT NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
    key TEXT NOT NULL,        -- Block des Laufs (Kachel, Abschnitt, Matrix-Block)
    row_key TEXT NOT NULL,    -- Zeile; '__total__' für die Summenzeile
    column_id TEXT NOT NULL,
    value REAL,               -- NULL bleibt NULL („–“)
    PRIMARY KEY (run_id, key, row_key, column_id)) WITHOUT ROWID;
ALTER TABLE runs ADD COLUMN origin TEXT NOT NULL DEFAULT 'manual';   -- 'manual' | 'schedule'
ALTER TABLE runs ADD COLUMN instance TEXT;                           -- abweichende Instanz des Laufs
CREATE TABLE schedules (
    id TEXT PRIMARY KEY,
    definition_id TEXT NOT NULL REFERENCES definitions(id) ON DELETE CASCADE,
    instance TEXT,                         -- NULL: wie in der Definition
    every TEXT NOT NULL,                   -- 'daily' | 'weekly' | 'monthly'
    at TEXT NOT NULL,                      -- 'HH:MM' in WDA_TIMEZONE
    weekday INTEGER, day INTEGER,          -- weekly: 0–6 (Mo–So); monthly: 1–28
    enabled INTEGER NOT NULL DEFAULT 1,
    next_due TEXT NOT NULL,                -- UTC, ISO
    attempts INTEGER NOT NULL DEFAULT 0,
    last_run_id TEXT, last_status TEXT, last_error TEXT, last_started_at TEXT,
    created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
```

- Migration 1 → 2 beim Öffnen in einer Transaktion: Tabellen/Spalten anlegen, `points` aus vorhandenen `run_results` nachtragen (seitenweise, beschädigte Ergebnisse überspringen und protokollieren), Version setzen. Eine Datenbank mit Version 2 wird von einer älteren App wie bisher abgelehnt.
- Grenzen: je Lauf höchstens 60.000 Punkte (mehr: Lauf wird gespeichert, Punkte nicht, Hinweis am Lauf); Aufbewahrung wie bisher über `WDA_RUNS_KEEP` je Definition, Punkte fallen mit dem Lauf.
- Was ein Punkt ist: jede Zelle `rows[i].values[j]` und die Summenzeile jedes Ergebnisses; bei Matrix-Abschnitten je Block wie gespeichert. Berechnete Spalten werden mitgeschrieben (so wie sie im Lauf standen).

## API

| Route | Zweck |
|---|---|
| `GET /api/definitions/{id}/history?key=&row=&column=&from=&to=&limit=` | Zeitreihe: `{series: [{key,row,column,label}], points: [{run_id, started_at, status, values: [...]}], notes}`. `row`/`column` mehrfach (höchstens 8 Reihen zusammen); ohne Angabe die Summenzeile der ersten Spalten. Namen stammen aus dem jüngsten Lauf. |
| `GET /api/definitions/{id}/history/keys` | Was sich auswählen lässt: Blöcke, Zeilen, Spalten des jüngsten Laufs (Schlüssel und Namen). |
| `GET /api/runs/{id}/report?compare=previous\|<run_id>\|<YYYY-MM-DD>` | Bericht mit Vergleich: je Zeile `previous: [...]`; im Kopf `compare: {run_id, started_at}`; Hinweis, wenn Definition, Version der Definition oder Instanz zwischen beiden Läufen abweichen, wenn Zeilen dazugekommen oder weggefallen sind. Datum: letzter Lauf derselben Definition an oder vor diesem Tag. |
| `GET /api/runs/{id}/export?format=…&compare=…` | HTML, Markdown, PDF zeigen die Veränderung hinter der Zahl (`+12`, `−3`, `±0`, „neu“); CSV und JSON bekommen die Vorwerte als eigene Spalten/Felder. Ohne `compare` unverändert. |
| `POST /api/definitions/{id}/run` mit `wait=false` | 202 `{id, status: "running"}`; der Lauf wird wie sonst gespeichert. |
| `GET /api/runs/{id}` | solange der Auftrag läuft: `{id, status: "running", progress: {done, total}}`; nach einem Neustart des Prozesses 404 (Auftrag verloren, kein halber Lauf). |
| `GET/POST /api/schedules`, `PUT/DELETE /api/schedules/{id}`, `POST /api/schedules/{id}/run` | Zeitpläne; Antwort nennt `next_due`, letzten Lauf und letzten Fehler. Höchstens 50 Zeitpläne, je Definition und Instanz einer. |

## Planer

- Startet in `lifespan`, prüft alle 30 s, führt Fälliges **nacheinander** aus (`refresh=True`, `origin='schedule'`). Abschaltbar mit `WDA_SCHEDULER=off` (Tests, zweite Instanz).
- Nachholen: War die App zur fälligen Zeit aus, läuft der Zeitplan nach dem Start **einmal**, nicht für jeden verpassten Termin.
- Scheitert ein Lauf ganz (`failed`, z. B. Repositorium nicht erreichbar), folgt nach 15, 30, 60 Minuten ein neuer Versuch; danach wartet der Zeitplan auf den nächsten Termin. `partial` gilt als gelaufen. Der letzte Fehler steht am Zeitplan.
- `next_due` wird in `WDA_TIMEZONE` (Standard `Europe/Berlin`) berechnet und als UTC gespeichert; Sommerzeit: eine Uhrzeit, die es nicht gibt, rückt auf die nächste gültige; eine doppelte läuft einmal.
- Uhr und Schlaf sind einspeisbar, damit Tests ohne Warten laufen.

## Global Constraints

- Zeitreihen, Vergleiche und Zeitpläne enthalten nur Zählungen und Definitionen; alle neuen Routen stehen in `tests/test_routes_guard.py`.
- Eine Zeitreihe vergleicht nur, was vergleichbar ist: Läufe anderer Instanz sind eigene Reihen (Parameter `instance`, Standard: Instanz der Definition); ein Wechsel der Definitions-Version erscheint als Markierung in der Reihe und als Hinweis.
- `null` bleibt `null` (Lücke in der Linie, „–“ in der Tabelle); eine fehlende Zeile ist nicht 0.
- Lesen einer Zeitreihe über 500 Läufe unter 100 ms (Test mit erzeugten Daten, großzügige Grenze).
- Der Planer darf die App nie mitreißen: Jede Ausnahme wird protokolliert, der Zeitplan bekommt den Fehler, die Schleife läuft weiter. Beim Herunterfahren wird ein laufender Lauf abgebrochen, nicht halb gespeichert.
- Zeiten in der Oberfläche in Ortszeit des Browsers, in Exporten mit Zeitzone.

## Review Focus

1. Vergleich über eine Änderung der Definition hinweg (Zeile umbenannt, Spalte entfernt, Block neu): keine falschen Differenzen, sondern Hinweis und „neu“/„entfallen“.
2. Migration mit vorhandenen Läufen, mit beschädigtem Ergebnis, mit sehr großem Lauf; zweimaliges Öffnen.
3. Planer: Neustart mitten im Lauf, Uhr springt (Sommerzeit, Systemzeit rückwärts), Definition während des Laufs gelöscht, zwei fällige Zeitpläne.
4. Auftrag: gleichzeitige Aufträge derselben Definition, Abbruch, Speicher wächst nicht (fertige Aufträge verfallen).
5. Keine Datensätze, keine Zugangsdaten in Zeitreihen, Fehlertexten des Planers oder Protokollen.

---

### Task 1: Punkte und Migration

**Files:** Modify `backend/wda/store/db.py`, `backend/wda/store/runner.py`; Create `backend/wda/store/points.py`; Test `backend/tests/test_points.py`, `backend/tests/test_store_db.py`

- [ ] Tests: Lauf schreibt Punkte (Metrik, Dashboard, Matrix mit Blöcken, `null`, Summenzeile); Löschen des Laufs löscht Punkte; Grenze 60.000; Migration 1 → 2 trägt nach und übersteht beschädigte Ergebnisse; zweites Öffnen ändert nichts. Fehlschlag sehen, umsetzen, grün, Commit.

### Task 2: Zeitreihen

**Files:** Create `backend/wda/store/history.py`, `backend/wda/api/history.py`; Modify `backend/wda/main.py`, `backend/tests/test_routes_guard.py`; Test `backend/tests/test_history.py`

- [ ] Tests: Reihe über mehrere Läufe, Auswahl von Zeilen/Spalten, `from`/`to`/`limit`, andere Instanz getrennt, Versionswechsel markiert, unbekannte Schlüssel 422 mit Auswahl, 500 Läufe schnell. Umsetzen, grün, Commit.

### Task 3: Vergleich

**Files:** Create `backend/wda/report/compare.py`; Modify `backend/wda/api/report.py`, `backend/wda/api/export.py`, `backend/wda/report/formats.py`; Test `backend/tests/test_compare.py`

- [ ] Tests: `previous`, Lauf-ID, Datum; neue/entfallene Zeilen und Spalten; Hinweise bei anderer Version/Instanz/Definition; kein Vorlauf → 404 mit Satz; Formate mit und ohne Vergleich. Umsetzen, grün, Commit.

### Task 4: Aufträge und Zeitpläne

**Files:** Create `backend/wda/store/schedules.py`, `backend/wda/scheduler.py`, `backend/wda/jobs.py`, `backend/wda/api/schedules.py`; Modify `backend/wda/api/runs.py`, `backend/wda/config.py`, `backend/wda/main.py`, `backend/requirements*.txt`; Test `backend/tests/test_schedules.py`, `backend/tests/test_scheduler.py`, `backend/tests/test_jobs.py`

- [ ] Tests: nächster Termin (täglich, wöchentlich, monatlich, Sommerzeit), Nachholen einmal, Wiederholung nach Fehlschlag, Reihenfolge, Fehler reißt die Schleife nicht mit, Auftrag mit Fortschritt und Verfall. Umsetzen, grün, Commit.

### Task 5: Oberfläche

**Files:** Create `frontend/src/app/history/*`, `frontend/src/app/schedules/*`; Modify `frontend/src/app/runs/*`, `frontend/src/app/charts/options.ts`, `frontend/src/app/app.routes.ts`

- Ansicht einer Definition bekommt „Verlauf“: Auswahl von Block, Zeilen, Spalten; Linie über die Zeit (Zeitachse, höchstens 8 Reihen, Versionswechsel als senkrechte Markierung), darunter die Tabelle der Punkte mit Link zum Lauf.
- Lauf-Seite: „Vergleichen mit“ (voriger Lauf, Lauf wählen, Datum); Veränderung neben jeder Zahl, Export-Links übernehmen den Vergleich.
- Zeitplan je Definition (Bibliothek und Ansicht) und Übersicht aller Zeitpläne unter „Läufe“; „Jetzt ausführen“.
- Lange Läufe: Start als Auftrag, Fortschrittsanzeige, Abbrechen verlässt nur die Anzeige.
- [ ] Tests: Zeitreihe → ECharts-Angaben (Lücken, Markierung), Veränderung formatieren (`+12`, `−3`, `±0`, „neu“), Zeitplan-Formular; Durchstich erweitert. Grün, Commit.

### Task 6: Review und Live-Probe

- [ ] Review durch frischen Reviewer, Befunde beheben.
- [ ] Live auf Jans Rechner: Zeitplan für die OER-Statistik, zwei Läufe, Verlauf und Vergleich ansehen.
