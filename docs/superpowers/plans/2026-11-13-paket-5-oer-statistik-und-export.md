# Paket 5: OER-Statistik und Export – Umsetzungsplan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ein mitgeliefertes Template bildet die OER-Statistik nach (Fächer in Fächergruppen × Bildungsbereiche × Quelle × Lizenzkategorien). Jeder gespeicherte Lauf lässt sich als JSON, CSV, Markdown, HTML und PDF abrufen.

**Architecture:** Templates bekommen einen Abschnittstyp `matrix`: eine Tabelle mit gegliederten Zeilen und Spaltenblöcken. Jeder Block ist eine Quelle: Abfrage an die Instanz, Abruf bei OERSI oder Handeintrag. Der Runner zerlegt einen Abfrage-Block in zwei Abfragen der Engine (Einzelzeilen als Facette, Zwischensummen als Gruppen). Eine Berichtsschicht setzt aus den gespeicherten Ergebnissen ein neutrales Tabellenmodell zusammen; die fünf Ausgabeformate lesen nur dieses Modell.

**Tech Stack:** wie bisher, neu WeasyPrint für PDF (Abweichung vom Design, das Headless-Chromium nannte: Grafiken entstehen serverseitig als SVG, damit sind HTML und PDF ohne Browser identisch und das Image bleibt klein).

**Spec:** Design Abschnitte 5.4, 6, 8 und Anhang A; `docs/edu-sharing-api-erkenntnisse.md` (Lizenzen, Quellen, OERSI direkt).

## Abschnitt `matrix`

```
{id, type: "matrix", title,
 rows:   {property, sections: [{id, label, values: [Wert], extra: [Wert] = []}],
          missing: bool = true, subtotal: "distinct" | "none" = "distinct"},
 series: [Series]                          wie in Paket 3, gilt in jedem Block
 blocks: [{id, label, source: "query", filters: [Filter]}
        | {id, label, source: "oersi", filters: {…}, section_map: "hochschulfaechersystematik"}
        | {id, label, source: "manual", as_of: Datum?, note: str?,
           totals: {series_id: Zahl}, cells: {zeilen_key: {series_id: Zahl}} = {}}]}
```

- `values` einer Zeilengruppe erscheinen als eigene Zeilen; `extra` zählt nur in der Zwischensumme mit (z. B. die Hochschulfächer einer Fächergruppe).
- Zwischensumme `distinct`: jedes Material einmal je Gruppe (eigene Zählabfrage), keine Zeilensumme.
- Gesamtzeile je Block: die Zählung des Blocks über alle Materialien, nie eine Summe.

## Tabellenmodell

```
Table {key, title, header_groups: [{label, span}]?, columns: [{id, label, derived}],
       rows: [{key, label, level: 0|1, kind: value|group|other|missing|total, values: [Zahl|null]}],
       notes: [{code, text}], sources: [{label, kind, instance?, fetched_at?, as_of?, note?}]}
```

## Global Constraints

- Exporte entstehen ausschließlich aus gespeicherten Läufen, enthalten also nur Zählungen, Definitionen und Texte der Definition. Die Export-Route liegt unter `/api` hinter der Sperre.
- Jede Zahl in jedem Format trägt ihre Herkunft: Quelle, Instanz, Abrufzeitpunkt, Hinweise der Engine; berechnete Spalten und Handeinträge sind als solche gekennzeichnet.
- Fehlende Werte (`null`) erscheinen als „–", nie als 0. Zahlen im deutschen Format (Tausenderpunkt, Dezimalkomma) in Markdown, HTML, PDF; in CSV und JSON roh.
- CSV: UTF-8 mit BOM, Trennzeichen `;` (einstellbar), Zellen, die mit `= + - @` beginnen, werden entschärft (kein Formel-Einschleusen über Labels).
- HTML: alles aus Definition und Repositorium wird maskiert; kein Skript, keine externen Ressourcen; Markdown-Text wird mit einer kleinen eigenen Umsetzung (Überschriften, Absätze, Listen, fett, kursiv) übertragen, rohes HTML darin bleibt Text.
- PDF entsteht aus demselben HTML; der Renderer darf keine Netz- oder Dateizugriffe machen (`url_fetcher`, der alles ablehnt).
- Externe Quellen: nur Zählungen (`size: 0`), fester Host je Quelle, Zeitlimit, Cache wie beim Repositorium; Ausfall einer externen Quelle lässt die übrigen Blöcke stehen (Block mit Fehler, Lauf `partial`).
- Platzhalter im Text: `{{abschnitt.total.spalte}}`, `{{abschnitt.total.block.spalte}}`, `{{abschnitt.cell.zeile.block.spalte}}` (so umgesetzt; nur für Spalten, die die Tabelle zeigt), `{{run.date}}`, `{{run.instance}}`. Unbekannte Platzhalter bleiben sichtbar stehen und erzeugen einen Hinweis; nichts wird ausgewertet.
- Die Zuordnungen des OER-Templates (Bildungsstufen → Bereich, Lizenz → Kategorie, Fach → Fächergruppe) sind Daten der Definition, kein Code.

## OER-Template (mitgeliefert, `builtin/oer-statistik.json`)

- Zeilen: `ccm:taxonid`, zehn Fächergruppen aus Anhang A mit den Schulfächern als Zeilen; die Hochschulfächer (Destatis-Systematik, Teilbaum je Fächergruppe) zählen als `extra` in der Zwischensumme.
- Blöcke (Abfrage): Elementarbereich; Schule (Primarstufe, Sekundarstufe I, Sekundarstufe II); Beruf und Erwachsene (Berufliche Bildung, Erwachsenenbildung, Fortbildung); Hochschule. Dazu OERSI (direkt) und Handeintrag für Mundo und Kita.bayern.
- Spalten je Block: Gesamt; A = `license` ∈ {OPEN, CC_BY_OPEN}; B = `license` = CC_BY_RESTRICTED plus Schlüssel `CC_BY_SA_NC`; mit Lizenzangabe = Schlüssel gesetzt; C = mit − A − B; D = Gesamt − mit; „OER gerundet" = A auf Hunderter.
- `CUSTOM`, `NONE` und leere Schlüssel zählen damit zu C, bis Jan anders entscheidet (offene Frage); der Methodik-Text nennt das.
- Methodik-Abschnitt mit Zählregeln und Abrufdatum als Text mit Platzhaltern.

## Review Focus

1. Ein Label wie `=HYPERLINK(...)` oder `<script>` als Titel, Gruppenname oder Facettenwert: in CSV entschärft, in HTML/PDF maskiert.
2. Lauf mit Fehler in einem Block und mit `null`-Zellen (gekürzte Facette): jede Ausgabe zeigt „–" und den Fehler, keine 0, kein Absturz.
3. Zwischensumme einer Fächergruppe ist nie größer als die Gesamtzahl des Blocks und nie kleiner als die größte Einzelzeile; Gesamtzeile ist die echte Zählung.
4. OERSI antwortet langsam, mit 5xx oder unerwartetem JSON: Block-Fehler, kein 500; es werden nie Treffer angefordert.
5. Läufe, die vor diesem Paket gespeichert wurden (ohne `matrix`), und Läufe mit anderer `schema_version`: lesbar bzw. klare Meldung.

---

### Task 1: Tabellenmodell und Zusammenbau

**Files:** Create `backend/wda/report/__init__.py`, `backend/wda/report/model.py`, `backend/wda/report/build.py`; Test `backend/tests/test_report_build.py`

- `build_report(run: dict) -> Report` mit `title, kind, started_at, status, blocks: [Text | Table | Failure]`; Engine-Ergebnis → `Table`; Platzhalter.
- [ ] Tests mit Läufen aus dem Runner (Kennzahl, Dashboard, Template mit Text) und mit Fehler-Kacheln; grün; Commit.

### Task 2: Abschnitt `matrix` im Store und im Runner

**Files:** Modify `backend/wda/store/specs.py`, `backend/wda/store/runner.py`, `backend/wda/report/build.py`; Test `backend/tests/test_matrix.py`

- Spezifikation wie oben; Runner plant je Abfrage-Block `abschnitt/block/rows` und `abschnitt/block/groups`; Handeintrag wird ohne Abruf zum Ergebnis.
- Zusammenbau zur gegliederten Tabelle mit Spaltenblöcken, Gesamtzeile, „ohne Wert".
- [ ] Tests gegen `DataRepo` mit `brute` als Sollwert für Einzelzeilen, Zwischensummen (auch mit `extra`), Gesamtzeile; Review-Focus 3; grün; Commit.

### Task 3: OERSI

**Files:** Create `backend/wda/external/__init__.py`, `backend/wda/external/oersi.py`; Modify `backend/wda/services.py`, `backend/wda/config.py`, `backend/wda/store/runner.py`; Test `backend/tests/test_oersi.py`

- `OersiClient(http, cache, base_url, timeout)`: `counts(filters) -> {total, by_license: {Schlüssel: Zahl}, by_subject_license?}` über `_search` mit `size: 0`; Lizenz-URLs → WLO-Schlüssel (`…/by-sa/4.0/` → `CC_BY_SA`, `…/publicdomain/zero/…` → `CC_0`, unbekannt → `CUSTOM`).
- Block `oersi` liefert die Spalten des Templates aus den Lizenzschlüsseln (Kategorie-Zuordnung als Teil des Blocks: `categories: {series_id: [Schlüssel]}`), Zeilen über die Destatis-Notation.
- `WDA_OERSI_URL` (Standard `https://oersi.org/resources/api/search/oer_data/_search`).
- [ ] Tests mit nachgebauter OERSI-Antwort: Normalisierung, Fehlerbilder, nie `size > 0`; grün; Commit.

### Task 4: Ausgabeformate

**Files:** Create `backend/wda/report/{text,csv_out,markdown,html,charts,pdf}.py`; Modify `backend/pyproject.toml`, `Dockerfile`, Anforderungsdateien; Test `backend/tests/test_report_formats.py`

- `to_json`, `to_csv`, `to_markdown`, `to_html`, `to_pdf`; Grafiken als SVG (Balken, gestapelte Balken, Kreis, Linie) aus der `view` der Kachel.
- [ ] Tests: Review-Focus 1, 2, 5; Zahlenformat; PDF beginnt mit `%PDF` und enthält keine externen Zugriffe; grün; Commit.

### Task 5: Routen und mitgeliefertes Template

**Files:** Create `backend/wda/api/export.py`, `backend/wda/api/builtin.py`, `backend/wda/builtin/oer-statistik.json`, `backend/scripts/build_oer_template.py`; Modify `backend/wda/main.py`, `README.md`; Test `backend/tests/test_export_routes.py`, `backend/tests/test_builtin.py`

- `GET /api/runs/{id}/export?format=json|csv|md|html|pdf` (mit `Content-Disposition`, Dateiname aus Name und Datum, bereinigt).
- `GET /api/builtin`, `POST /api/builtin/{id}/install` (legt die Definitionen an; erneutes Installieren ersetzt nichts, sondern meldet „vorhanden").
- Das Skript erzeugt das Template aus den Vokabular-Dateien (Schulfächer, Destatis-Systematik) und Anhang A; das Ergebnis liegt im Repo.
- [ ] Tests: jede Route; das mitgelieferte Template ist gültig, alle Zeilen aus Anhang A sind zugeordnet, jeder Hochschul-Teilbaum liegt in genau einer Fächergruppe; Lauf des Templates gegen `DataRepo` ergibt eine Tabelle; grün; Commit.
- [ ] Live-Lauf gegen WLO Produktion über Jans Rechner, Vergleich der Größenordnungen mit der Vorlage (Stand Oktober 2025).
