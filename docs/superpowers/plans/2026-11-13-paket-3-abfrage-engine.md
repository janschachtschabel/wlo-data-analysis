# Paket 3: Abfrage-Engine – Umsetzungsplan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Eine Abfrage-Beschreibung (`QuerySpec`) in möglichst wenige Zähl- und Facetten-Abfragen zerlegen und daraus eine Tabelle mit Herkunftsangabe bauen. Das ist das einzige Format, in dem Kennzahlen, Dashboards und Templates später Zahlen anfordern.

**Architecture:** Pydantic-Modelle beschreiben die Abfrage. Die Engine plant daraus Aggregat-Aufrufe an den Client aus Paket 2 (der sie cacht und zusammenlegt), führt sie parallel aus und setzt die Zellen zusammen. Abgeleitete Spalten entstehen über Formeln aus anderen Spalten.

**Tech Stack:** wie Paket 2; Formeln über `ast` mit fester Positivliste.

**Spec:** Design Abschnitt 5.3; gemessene Zähl-Semantik in `docs/edu-sharing-api-erkenntnisse.md`.

## Abfrage-Format

```
QuerySpec
  instance: str                      content_type: str = "FILES"
  filters:  [Filter]                 Grundmenge, UND-verknüpft
  rows:     Rows | None              Zeilen-Aufschlüsselung
  series:   [Series]                 Spalten, jede mit eigenen Zusatzfiltern oder Formel
  columns:  Columns | None           Alternative zu series: eine Spalte je Wert eines Feldes
  percent:  "none" | "column" | "row" = "none"
  refresh:  bool = False

Filter   {property, values: [str | {value, literal: true}]}
Rows     {property, include: [Wert]?, groups: [{id?, label, values}]?,
          group_mode: "auto" | "sum" | "distinct" = "auto",
          sort: "count" | "label" | "given" = "count", top: int?, other: bool = false,
          missing: bool = false}
Series   {id, label, filters: [Filter] = [], formula: str?}
Columns  {property, include: [Wert]?, top: int = 12}
```

Ergebnis:

```
{"instance", "columns": [{"id","label","derived","total"}],
 "rows": [{"key","label","kind","values":[Zahl|null],"percent":[Zahl|null]?}],
 "provenance": {"fetched_at","oldest_fetched_at","cached","queries","notes":[…],
                "filters":[aufgelöste Filter]}}
```

`kind` ∈ `value`, `group`, `other`, `missing`. `columns[].total` ist die Zählung der Spalte über alle Dokumente (jedes einmal), keine Zeilensumme.

## Global Constraints

- Nach dem Review geändert: Ob Summen exakt sind, hängt nicht am Feldtyp des Metadatensets, sondern an `prefix_fields` der Instanz (einwertige Felder mit Präfix-Treffer, Standard: Lizenzschlüssel). Nur dort wird summiert; für alle anderen Felder sind Gruppen (`auto`), „Übrige" und „ohne Wert" eigene Zählabfragen. Ein Filter auf einem Präfix-Feld ist nur mit einem Wert erlaubt, der kein Anfang eines anderen vorkommenden Schlüssels ist. Steht das Zeilenfeld in den Grundfiltern, sind Gruppen, „Übrige" und „ohne Wert" ein Fehler. Anteile gibt es nicht für Spalten mit Formel.
- Die Engine ruft ausschließlich `EduSharingClient.aggregate` auf; sie kann keine Datensätze sehen.
- Zeilen ohne Gruppen und Gruppen im Modus `sum` kommen aus **einer** Facetten-Abfrage je Spalte. `sum` addiert Facettenwerte und ist nur für einwertige Felder exakt.
- Gruppen im Modus `distinct` kosten eine Zählabfrage je Gruppe und Spalte (Werte der Gruppe als ein Kriterium, ODER). `auto` wählt `distinct`, wenn der Feldtyp im Metadatenset mit `multivalue` beginnt, sonst `sum`.
- Steht das Zeilenfeld auch in den Grundfiltern, zeigen die Zeilen nur die gefilterten Werte (die Facette ignoriert den Filter auf dem eigenen Feld). Mit Gruppen im Modus `distinct` oder mit `missing` ist diese Kombination ein Fehler.
- Ein Spaltenfilter darf kein Feld der Grundfilter und nicht das Zeilenfeld verwenden (das Repositorium nähme still nur ein Kriterium).
- `missing` = Spalten-Gesamtzahl minus Zählung mit allen vorkommenden Werten des Zeilenfeldes als ein Kriterium. Ist die Facette gekürzt, entfällt die Zeile mit Hinweis.
- Formeln: Namen anderer Spalten, Zahlen, `+ - * /`, Klammern, `round(x, n)`, `max`, `min`. Division durch 0 und fehlende Werte ergeben `null`. Formeln dürfen auf Formeln verweisen, Zyklen sind ein Fehler.
- Grenzen: höchstens 40 Spalten, 300 Gruppen, 400 Aggregat-Aufrufe je Abfrage; Überschreitung ist ein Fehler vor dem ersten Aufruf.
- Jede Abweichung von der naiven Erwartung steht als Satz in `provenance.notes` (gekürzte Facette, Modus `sum`, abgeleitete Spalten, eingeschränkte Zeilen).
- Fehler in der Abfrage sind `QueryError` → HTTP 422 mit deutscher Meldung.

## Review Focus

1. Dokumente mit mehreren Fächern: Zeilensummen übersteigen die Spalten-Gesamtzahl; `total` muss die echte Zählung sein.
2. Gruppen mit überlappenden Werten oder Werten, die im Bestand nicht vorkommen: Zählung 0 bzw. korrekt, kein Fehler.
3. Formel verweist auf unbekannte Spalte, sich selbst oder teilt durch 0.
4. `top` mit `other`: „Übrige" darf bei mehrwertigen Feldern nicht als Summe ausgegeben werden, sondern entfällt mit Hinweis (Summe wäre keine Dokumentzahl).
5. Leere Treffermenge: Tabelle mit 0 Zeilen und Gesamtzahl 0, keine Division durch 0 bei Anteilen.

---

### Task 1: Testbestand mit echter Zähl-Semantik

**Files:** Create `backend/tests/esdata.py`; Test `backend/tests/test_esdata.py`

- `DataRepo(docs: list[dict[str, list[str]]], mds: dict)`: Transport, der Suchabfragen gegen einen kleinen Bestand auswertet: ODER innerhalb eines Kriteriums, UND zwischen Kriterien, Präfix-Treffer für `ccm:commonlicense_key`, Gruppen für `license`, Facette ignoriert das Kriterium auf dem eigenen Feld, `facetLimit` × 5 mit `sumOtherDocCount`, 400 bei unbekanntem Feld. `brute(pred) -> int` zählt direkt.
- [ ] Tests für jede dieser Regeln gegen die Zahlenverhältnisse aus dem Erkenntnis-Dokument; grün; Commit.

### Task 2: Formeln

**Files:** Create `backend/wda/engine/__init__.py`, `backend/wda/engine/formula.py`; Test `backend/tests/test_formula.py`

- `compile_formula(text: str) -> Formula` mit `names: frozenset[str]` und `evaluate(values: dict[str, float | None]) -> float | None`; `QueryError` bei allem außerhalb der Positivliste (Attribute, Aufrufe anderer Funktionen, Zeichenketten, Potenzen, Vergleiche, mehr als 500 Zeichen).
- [ ] Tests: Arithmetik, Vorrang, `round(x, -2)`, `None`-Weitergabe, Division durch 0, abgelehnte Ausdrücke (`__import__('os')`, `a.b`, `a**9**9`, `[1]`, `"x"`); grün; Commit.

### Task 3: Modelle und Engine

**Files:** Create `backend/wda/engine/spec.py`, `backend/wda/engine/engine.py`; Test `backend/tests/test_engine.py`

**Interfaces – Produces:**

- Pydantic-Modelle wie oben (`extra="forbid"`), `QueryError(message)`.
- `async run_query(spec: QuerySpec, client: EduSharingClient, vocab: VocabStore) -> dict`.

- [ ] Tests gegen `DataRepo`, jeweils mit `brute` als Sollwert: nur Gesamtzahl; Zeilen als Facette; Zeilen × Spalten-Filter; `columns` aus einem Feld; Gruppen `distinct` (überlappend, mit unbekanntem Wert); Gruppen `sum` auf dem Lizenzschlüssel; `auto` wählt je nach Feldtyp; `missing`; Zeilenfeld in den Grundfiltern; `include`, `top`, `other` (einwertig: Summe, mehrwertig: entfällt mit Hinweis); Sortierungen; Formeln inklusive Kette und Zyklus; `percent` nach Spalte und Zeile; leere Treffermenge; Label-Auflösung in Filtern, Gruppen und `include`; alle Fehlerfälle aus den Global Constraints; Anzahl der Aggregat-Aufrufe für die OER-Form (4 Bereiche × 4 Lizenzspalten als 4 Abfragen mit je 5 Spalten: höchstens 5 Aufrufe je Bereich).
- [ ] Fehlschlag sehen; umsetzen; grün; Commit.

### Task 4: Route

**Files:** Create `backend/wda/api/query.py`; Modify `backend/wda/main.py`; Test `backend/tests/test_query_route.py`

- `POST /api/query` (hinter `require_admin`) nimmt eine `QuerySpec`, liefert das Ergebnis; `QueryError` → 422 `{"detail", "kind": "invalid_query"}`; unbekannte Instanz 404.
- [ ] Tests: Erfolg, 422, 404, 401 ohne Login; grün; Commit.
