# Paket 11: Lückenliste, weitere Ansichten, nächtliches Nachziehen

## Ziel

Drei Ideen aus der Liste nach Paket 8c und 10:

1. **Lückenliste.** Die Matrix Fach × Bildungsstufe zeigt Lücken nur dem, der sie sucht.
   Eine Liste nennt die Felder, die am weitesten unter dem Erwarteten bleiben.
2. **Weitere Kacheln in „Abdeckung“:** Anteil offener Lizenzen je Schulfach, Lizenzen je
   Fächergruppe der Hochschule, redaktionelle Erschließung je Schulfach, Sprachniveau je
   Bildungsstufe.
3. **Nächtliches Nachziehen** vorhandener Abzüge.

## Lückenliste (`gaps`)

- `QuerySpec.gaps = {limit: 30 (≤ 200), min_expected: 20}`; braucht `rows` und `columns`,
  nicht zusammen mit `fill`, `vocab_check`, `profile`, `sizes`, `percent`, `snapshot`.
- `engine._gaps` rechnet nach der gewöhnlichen Abfrage, also ohne weitere Aufrufe:
  Erwartung je Feld = Zeilensumme × Spaltensumme ÷ Summe der gezeigten Matrix; nur Zeilen
  der Art `value`. Sortiert nach Anteil am Erwarteten, dann nach größerer Erwartung.
- Ergebnis hat die Spalten `count`, `expected`, `ratio`, keine Summenzeile, Hinweis `gaps`.
- Grenzen, die der Hinweis nennt: Summen stammen aus der gezeigten Matrix (Mehrfachwerte
  zählen mehrfach); eine Lücke kann fachlich begründet sein.

## Nächtliches Nachziehen

- `WDA_SNAPSHOT_NIGHTLY` (Standard `03:30`, `off` schaltet ab), Uhrzeit in `WDA_TIMEZONE`.
- `Services.nightly_snapshots(now)` läuft mit jedem Durchgang des Planers. Für jede
  Instanz mit vollständigem Abzug, der älter ist als der letzte fällige Zeitpunkt: einmal
  `update`. Der Versuch wird im Abzug vermerkt (`nightly_at`), ein Fehlschlag wiederholt
  sich erst in der nächsten Nacht. Es wird nie ein Abzug angelegt.
- Start des Abzugs liegt jetzt in `Services.start_sync`, Route und Planer nutzen dasselbe.

## Tests

`tests/test_engine_gaps.py`, `tests/test_builtin.py` (neue Kacheln, Lückenliste gegen die
Matrix), `tests/test_snapshot_sync.py` (nächtliches Nachziehen, Abschalten).

## Worauf das Review achten soll

1. Rechnet die Lückenliste richtig, auch mit leeren Feldern, „Übrige“, rollup, Zeilen
   ohne Spaltenwerte? Verträgt sich das Ergebnis mit Exporten, Historie, Assistent?
2. Nächtliches Nachziehen: Kann es mehrfach, gar nicht oder gleichzeitig mit einem
   Abzug von Hand starten? Zeitzonen, Sommerzeit, lange Standzeit der App.
3. Neue Kacheln: sinnvolle Abfragen, Aufwand je Lauf (Zahl der Abfragen).
