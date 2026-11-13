# Paket 8: Datenqualität I

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:executing-plans. Jede Aufgabe testgetrieben, danach Review durch einen frischen Reviewer und Befundbehebung. Nach diesem Paket folgt das übergreifende Review der Pakete 6 bis 8.

**Goal:** Aussagen zur Datenqualität, soweit sie sich aus Zählungen ergeben: Wie oft ist ein Feld gefüllt (insgesamt und je Quelle), wie verteilen sich Werte und Labels, wo fehlen Angaben. Ohne Sync, ohne einen einzigen Datensatz.

**Architecture:** Füllgrade sind eine dritte Form der Spalten einer Abfrage (`QuerySpec.fill`, neben `series` und `columns`). Damit funktionieren sie überall, wo eine Abfrage steht: Explorer, Kennzahl, Kachel, Abschnitt, Lauf, Verlauf, Vergleich, Export. Gezählt wird je Feld „Materialien mit mindestens einem Wert“; „ohne Wert“ ist die Differenz zur Gesamtzahl.

**Tech Stack:** wie bisher (FastAPI, Engine, Angular, ECharts).

## Was die Suche hergibt (gemessen, siehe `docs/edu-sharing-api-erkenntnisse.md`)

- Facetten zählen nur Dokumente mit Wert. Mehrwertiges Feld: „mit Wert“ = Zählung mit allen vorkommenden Werten als ein Kriterium (ODER). Einwertiges Präfix-Feld (Lizenzschlüssel): Kriterium mit leerem Wert `""` zählt alle mit gesetztem Schlüssel.
- Ein Kriterium „Feld fehlt“ gibt es nicht; „ohne Wert“ ist immer abgeleitet.
- Grenze: Felder mit mehr als 5.000 verschiedenen Werten oder gekürzter Werteliste lassen sich so nicht zählen (z. B. Schlagwörter). Dann steht „–“ mit Hinweis; Füllgrade beliebiger Felder (Titel, Beschreibung) kommen erst mit dem Sync in Paket 10.
- Nur Felder, die als Facette freigegeben sind (Werteliste im Metadatenset oder Freigabeliste der Instanz).

## QuerySpec.fill

```json
{"instance": "wlo-prod", "filters": [],
 "rows": {"property": "ccm:replicationsource", "top": 25},
 "fill": {"fields": [{"property": "ccm:taxonid"}, {"property": "ccm:commonlicense_key", "label": "Lizenz"}],
          "show": "rate"}}
```

- `fields`: 1 bis 30, je Feld `property` und optional `label` (sonst der Name aus dem Metadatenset). `show`: `rate` (Füllgrad in Prozent, Standard), `missing` (Zahl ohne Wert) oder `filled` (Zahl mit Wert); wirkt nur zusammen mit `rows`.
- `fill` schließt `series` und `columns` aus; `percent` bleibt `none`.
- **Ohne `rows`:** eine Zeile je Feld (Schlüssel = Feldname), Spalten `filled` „mit Wert“, `missing` „ohne Wert“, `rate` „Füllgrad %“ (berechnet). Keine Gesamtzeile (`total_row: false` im Ergebnis); die Grundgesamtheit steht als Hinweis (`fill_base`) und in `base_total`.
- **Mit `rows`:** Zeilen wie sonst (z. B. je Quelle), Spalte `count` „Anzahl“ und je Feld eine Spalte (ID aus dem Feldnamen, stabil über Läufe). Gesamtzeile wie sonst.
- Steht ein Feld schon in den Filtern, hat jedes gefilterte Material dort einen Wert: 100 %, Hinweis `fill_filtered`. Ein Feld, das zugleich Zeilenfeld ist, wird abgelehnt.
- Nicht bestimmbar (zu viele Werte, gekürzt): Zellen `null`, Hinweis `fill_unknown` mit den Feldnamen.
- Kosten: je Feld höchstens zwei Aufrufe (Werteliste, Zählung); zählt gegen `MAX_CALLS`.
- Füllgrad: `round(100 * filled / total, 1)`; bei Gesamtzahl 0 `null`.

## Oberfläche

- Explorer: Umschalter „Verteilung | Füllgrade“. Bei Füllgraden: Felder wählen (alle freigegebenen Felder der Instanz, Vorauswahl: Fach, Bildungsstufe, Materialart, Lizenz, Quelle, Sprache, soweit vorhanden), optional „je“ (Zeilenfeld) mit Anzeige Füllgrad / ohne Wert / mit Wert. Der Zustand steht in der Adresse; „Als Kennzahl speichern“ wie bisher.
- Darstellung: Tabelle; Balken zeigt bei Füllgraden ohne Zeilenfeld nur den Füllgrad (0 bis 100 %), gestapelt „mit Wert / ohne Wert“. Die Zahl-Kachel entfällt für Füllgrade.
- Berichte, Exporte, Verlauf und Vergleich brauchen nichts Eigenes; ohne Gesamtzeile wählt der Verlauf die erste Zeile.

## Mitgeliefert: Dashboard „Datenqualität“

Bundle `datenqualitaet` (zweites neben der OER-Statistik), nur Abfragen aus freigegebenen Feldern:

1. Füllgrade der wichtigsten Felder (gestapelte Balken)
2. Füllgrad je Quelle (`ccm:replicationsource`, 25 größte, Tabelle)
3. Fehlende Angaben je Quelle (Zahl ohne Wert, Tabelle)
4. Lizenzen: Verteilung mit „ohne Wert“ (Balken)
5. Quellen: Materialien je Quelle (Balken) und je Bezugsquelle (`ccm:oeh_publisher_combined`, 25 größte)
6. Verteilungen: Bildungsstufe, Materialart, Sprache; Redaktions-Kennzeichen, soweit die Instanz sie als Facette hergibt

Welche Felder WLO Produktion tatsächlich als Facette und Kriterium annimmt und wie viele Werte sie haben, wird vor dem Zusammenstellen live gemessen (Task 1) und in `docs/edu-sharing-api-erkenntnisse.md` festgehalten. Das Dashboard enthält nur, was dort funktioniert.

## Global Constraints

- Keine neue Route, kein neuer Speicher: alles sind Zählungen der bestehenden Engine. `tests/test_routes_guard.py` bleibt unverändert.
- „Ohne Wert“ und Füllgrad sind immer als abgeleitet gekennzeichnet (Hinweis); unbekannt ist `null`, nie 0 und nie 100 %.
- Die Zahl „mit Wert“ darf nie größer sein als die Gesamtzahl; ist sie es (Zählfehler des Repositoriums), wird auf die Gesamtzahl begrenzt und ein Hinweis gesetzt.
- Bestehende gespeicherte Definitionen und Läufe bleiben gültig (das Ergebnisformat bekommt nur optionale Felder).

## Review Focus

1. Richtigkeit gegen den Testbestand mit roher Zählung: Füllgrad je Feld und je Zeile, mit und ohne Grundfilter, einwertig und mehrwertig.
2. Grenzfälle: Feld in Filtern, Feld = Zeilenfeld, Feld ohne Werte (0 %), leere Grundgesamtheit, gekürzte Werteliste, über 5.000 Werte, doppelte Felder, unbekanntes oder nicht freigegebenes Feld.
3. Zusammenspiel: Lauf speichern, Punkte, Verlauf ohne Gesamtzeile, Vergleich, CSV/HTML/PDF, Grafik.
4. Sicherheit: `fill` darf keine Facette über nicht freigegebene Felder auslösen.

## Tasks

### Task 1: Live messen
- [ ] Auf Jans Rechner: alle Widgets mit Werteliste und die Freigabeliste gegen `ngsearch` als Facette und als Kriterium probieren, Zahl der Werte und Summe notieren; Kandidaten für Redaktions- und Qualitätskennzeichen. Ergebnis in die Erkenntnis-Datei.

### Task 2: Engine
**Files:** Modify `backend/wda/engine/spec.py`, `backend/wda/engine/engine.py`, `backend/wda/report/build.py`; Test `backend/tests/test_fill.py`
- [ ] Tests gegen rohe Zählung, dann Umsetzung, grün, Commit.

### Task 3: Oberfläche
**Files:** Modify `frontend/src/app/explorer/explorer.ts`, `frontend/src/app/filters/state.ts`, `frontend/src/app/charts/options.ts`, `frontend/src/app/core/types.ts`; Tests dazu; Durchstich erweitern.

### Task 4: Dashboard „Datenqualität“
**Files:** Create `backend/wda/builtin/datenqualitaet.json`; Modify `backend/wda/api/builtin.py` falls nötig; Test `backend/tests/test_builtin.py`

### Task 5: Review, Befundbehebung, Live-Probe; danach übergreifendes Review 6 bis 8
