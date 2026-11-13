# Paket 6: Oberfläche – Umsetzungsplan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Alles, was die API seit Paket 5 kann, ist im Browser bedienbar: anmelden, Instanz wählen, Filter aus Labels oder URIs bauen, Zahlen als Tabelle und Grafik sehen, als Kennzahl speichern, Dashboards und Templates verwalten, Läufe ansehen und exportieren. Schnell und ohne Einarbeitung.

**Architecture:** Angular-SPA (eigenständige Komponenten, Signals, keine Zustandsbibliothek) unter `frontend/`. Sie spricht ausschließlich `/api`. Der Bau landet in `backend/wda/static/` und wird vom Backend ausgeliefert (ein Container, wie im Design). Grafiken mit ECharts, nur die genutzten Module. Das Backend bekommt die wenigen Routen, die eine Oberfläche zusätzlich braucht.

**Tech Stack:** Angular 22, TypeScript strikt, ECharts 6, Vitest (Angular-Standard) für Unit-Tests, Playwright für wenige Durchstiche gegen das echte Backend mit nachgebautem Repositorium. Kein UI-Baukasten; eigenes kleines Stylesheet mit CSS-Variablen (hell/dunkel).

**Spec:** Design Abschnitte 2, 5.1, 5.4, 5.7.

## Seiten

| Pfad | Inhalt |
|---|---|
| `/login` | Passwort; danach zurück zur gewünschten Seite |
| `/` Explorer | Instanz, Filterleiste, Zeilen/Spalten/Darstellung, Ergebnis mit Herkunft; „Als Kennzahl speichern“ |
| `/bibliothek` | Gruppen links, Definitionen (Filter, Kennzahlen, Dashboards, Templates) mit Suche, Duplizieren, Verschieben, Löschen, Import/Export, mitgelieferte Vorlagen installieren |
| `/kennzahl/:id` | Explorer mit geladener Kennzahl |
| `/dashboard/:id` | Raster aus Kacheln, gemeinsame Filter, Lauf speichern, Bearbeiten-Modus (Kachel hinzufügen, Größe, Reihenfolge) |
| `/template/:id` | Abschnitte bearbeiten (Text, Tabelle, Grafik; Matrix als JSON-Editor mit Prüfung), Vorschau des Berichts |
| `/laeufe`, `/lauf/:id` | Liste; Bericht im Browser, Export in fünf Formaten |

## Filter-Baukasten (Kern der Bedienung)

- Ein Eingabefeld „Filter hinzufügen“: tippt man einen Feldnamen (Label oder Property), wählt man das Feld; danach Werte mit Autovervollständigung aus `/vocab` (Label, darunter klein die URI). Eine eingefügte URI wird als Wert erkannt und mit ihrem Label angezeigt.
- Jeder Filter ist ein Chip: Feld-Label, Werte-Labels; Tooltip und Umschalter zeigen die URIs. „URI kopieren“ je Wert.
- Freitext, der sich nicht auflösen lässt, zeigt die Vorschläge der API (`unresolved_value`) zur Auswahl; „wörtlich verwenden“ setzt `literal`.
- Der Zustand des Explorers steht vollständig in der URL (`?q=` als Base64-URL der QuerySpec), damit ein Link die Ansicht wiedergibt. Die URL enthält nur die Abfrage, nie Ergebnisse.

## Global Constraints

- Die Oberfläche bekommt und zeigt nur, was die API liefert: Zählungen, Definitionen, Vokabulare. Kein Zwischenspeicher im Browser außer dem Arbeitszustand der Seite (kein `localStorage` für Ergebnisse; nur Darstellungsvorlieben wie hell/dunkel).
- Alle Texte aus Repositorium und Definitionen werden als Text gebunden (keine `innerHTML`-Bindung). Berichtstexte kommen als fertiges, serverseitig maskiertes HTML nur über eine eigene Route und laufen dort durch denselben Markdown-Umsetzer wie der Export; im Browser zusätzlich durch Angulars Sanitizer.
- CSP vom Backend für die SPA: `default-src 'self'`; kein Inline-Skript; Grafiken über Canvas.
- Jede Zahl zeigt auf Wunsch ihre Herkunft (Instanz, Abrufzeit, Hinweise der Engine). Hinweise sind nie versteckt, wenn es welche gibt (Zähler am Ergebnis).
- `null` erscheint als „–“. Zahlen im deutschen Format.
- Jede Abfrage ist abbrechbar; eine neue Eingabe verwirft die laufende (kein Überholen alter Antworten). Eingaben lösen nach 250 ms Ruhe aus.
- 401 von der API führt zur Anmeldung und danach zurück; andere Fehler erscheinen mit `detail` der API am Ort des Geschehens, nie als leere Fläche.
- Bedienbar mit Tastatur; Fokus sichtbar; Kontraste AA; Grafiken haben immer die Tabelle als Alternative.
- Bau ist reproduzierbar (`package-lock.json`, `npm ci`), Budget für das Hauptbündel 500 kB gzip.

## Backend-Ergänzungen

- `GET /api/runs/{id}/report` → Berichtsmodell als JSON, Textabschnitte zusätzlich als maskiertes HTML (`html`). Dasselbe für einen ungespeicherten Lauf: `POST /api/definitions/{id}/run?save=false&report=true` bzw. `POST /api/preview` mit einer ungespeicherten Definition (`{kind, spec, instance?}`) → Lauf + Bericht, nichts wird gespeichert.
- Auslieferung der SPA: Dateien aus `static/` mit Cache-Kopfzeilen (gehashte Dateien lange, `index.html` nie), unbekannte Pfade außerhalb `/api` → `index.html`.
- Was das übergreifende Review 4–5 für die Oberfläche als fehlend meldet, kommt hier dazu.

## Review Focus

1. Ein Label oder Titel mit `<img onerror>` oder `javascript:` in jeder Ansicht: bleibt Text.
2. Schnelles Tippen und Wechseln der Instanz: Die Anzeige gehört immer zur letzten Eingabe; keine alten Zahlen unter neuen Filtern.
3. Sitzung läuft ab während der Arbeit: Anmeldung, danach ist der Arbeitszustand noch da.
4. Fehler einer Kachel lässt die anderen stehen; Fehlertext der API sichtbar.
5. URL-Zustand: kaputter oder riesiger `q`-Parameter → leerer Explorer mit Meldung, kein Absturz.
6. Kein Weg, über die Oberfläche oder ihre neuen Routen an Datensätze zu kommen; die neuen Routen stehen in `tests/test_routes_guard.py`.

---

### Task 1: Backend-Ergänzungen

**Files:** Create `backend/wda/api/report.py`, `backend/wda/api/spa.py`; Modify `backend/wda/main.py`, `backend/tests/test_routes_guard.py`; Test `backend/tests/test_report_routes.py`, `backend/tests/test_spa.py`

- [ ] Tests (Bericht eines Laufs, Vorschau ohne Speichern, maskiertes HTML, SPA-Fallback, CSP, Cache-Kopfzeilen); Fehlschlag sehen; umsetzen; grün; Commit.

### Task 2: Gerüst, API-Schicht, Anmeldung

**Files:** Create `frontend/` (Angular-Projekt), `frontend/src/app/core/{api.ts,auth.ts,format.ts,errors.ts}`; Modify `Dockerfile`, `.github/workflows/ci.yml`, `README.md`

- Typen der API von Hand in `core/types.ts`; `Api`-Dienst mit Abbruch und einheitlichem Fehlerobjekt; Wächter für Routen; Layout mit Navigation und Instanzwahl.
- [ ] Tests: Zahlenformat, Fehlerabbildung, 401-Umleitung, Wächter; grün; Docker-Bau enthält die SPA; Commit.

### Task 3: Filter-Baukasten und Explorer

**Files:** Create `frontend/src/app/explorer/*`, `frontend/src/app/filters/*`, `frontend/src/app/charts/*`

- [ ] Tests: Filter-Zustand (hinzufügen, entfernen, URI einfügen, unaufgelöster Wert mit Vorschlägen), QuerySpec ↔ URL, Verwerfen überholter Antworten, Ergebnis → ECharts-Optionen (Balken, gestapelt, Kreis, Linie), „–“ für `null`; grün; Commit.

### Task 4: Bibliothek, Kennzahlen, Läufe und Export

**Files:** Create `frontend/src/app/library/*`, `frontend/src/app/runs/*`

- [ ] Tests: Liste mit Gruppen und Suche, Speichern mit Versionskonflikt (409 → Hinweis und Neuladen), Löschen mit „wird verwendet von“, Import/Export, Bericht-Ansicht; grün; Commit.

### Task 5: Dashboards und Templates

**Files:** Create `frontend/src/app/dashboard/*`, `frontend/src/app/template/*`

- [ ] Tests: Raster-Anordnung, Kachel-Fehler isoliert, Bearbeiten und Speichern, Template-Abschnitte, Matrix-JSON mit Fehlermeldungen der API, Vorschau; grün; Commit.

### Task 6: Durchstiche und Feinschliff

**Files:** Create `frontend/e2e/*`, `backend/scripts/demo_server.py` (Backend mit nachgebautem Repositorium)

- [ ] Playwright: anmelden → Filter bauen → Kennzahl speichern → Dashboard → Lauf → Export HTML; mitgelieferte OER-Statistik installieren und ausführen; Review Focus 1 bis 5; Screenshots hell/dunkel ansehen; Bündelgröße prüfen; Commit.
- [ ] Live-Probe auf Jans Rechner gegen WLO.
