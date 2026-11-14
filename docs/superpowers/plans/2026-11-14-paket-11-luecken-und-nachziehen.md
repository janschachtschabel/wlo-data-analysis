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

## Stand nach Review (Commits 9af5dff und 30f1f0d gemeinsam geprüft)

Kein kritischer Befund. Behoben (Tests in `backend/tests/test_review_p11.py` und
`test_engine_gaps.py`):

- W1: Die Lückenliste nimmt auch Gruppenzeilen (rollup); die Kachel für die Hochschule
  war leer.
- W2: Nicht bestimmbare Felder zählen nicht als null; sie fehlen in Summen und Liste, der
  Hinweis nennt ihre Zahl. Die Liste zeigt nur Felder unter der Erwartung.
- W3: Die Vollständigkeitsprüfung am Ende eines vollen Laufs vergleicht mit dem Bestand
  von jetzt (eine Zählabfrage) und verwirft bei Abbruch den Zeiger, damit der nächste
  Start neu liest.
- W4: Nachts wird nichts nachgezogen, solange ein voller Lauf aufs Fortsetzen wartet.
- W5: `start_sync` prüft selbst, ob schon etwas läuft; zwei Abzüge derselben Instanz
  sind ausgeschlossen.
- W6: Löschen sperrt über `Services.clearing`; „Anhalten“ hebt die Sperre nicht auf.
- G1 Zeitumstellung (Vergleich von Zeitpunkten statt Uhrzeiten), G2 eine unlesbare
  Abzugsdatei hält die übrigen Instanzen nicht auf, G3 Text des Hinweises und
  eindeutiger Schlüssel, G4 Antwort auf Start ohne Reste des vorigen Laufs, G6 README.

Offen, gering: G5 Die beiden Lückenkacheln wiederholen die Abfragen ihrer Matrix; im
gewöhnlichen Lauf kommen sie aus dem Zwischenspeicher, bei Zeitplänen (`refresh`) kosten
sie die Aufrufe noch einmal. G7 `close()` wartet auf eine laufende Auswertung. In der
Historie wechselt die Zeilenmenge der Lückenliste je Lauf.

## Probe gegen WLO (Jans Rechner, Commit 49917e6) und Nachschärfen

Dashboard „Abdeckung“: 17 Kacheln ohne Fehler, 592 Abfragen, 9,2 s. Befunde aus den
echten Zahlen und was daraus wurde:

- Die Lückenliste füllte bis `limit` auch mit Feldern nahe 100 % auf. Neu: `below`
  (Standard 50): nur Felder unter so viel Prozent der Erwartung.
- Schule: Sammelstufen („Informelles Lernen“, „Schule“, „Fernunterricht“) bestimmten die
  Liste. Die Kachel nimmt jetzt nur Primarstufe, Sekundarstufe I und II, berufliche
  Bildung und Förderschule als Spalten.
- Hochschule: Fächergruppe gegen Bildungsstufe ergibt keine sinnvollen Lücken
  (Hochschulfächer fehlen erwartbar in Schulstufen). Ersetzt durch Schulfach ×
  Materialart (`luecken-schule-materialart`).
- Sprachniveau: 12 statt 8 Spalten, damit alle Stufen erscheinen.
- Echte Zahlen: Anteil offener Lizenzen über alle Schulfächer 28,9 %, bei
  Fächergruppen der Hochschule 87 bis 99 %. Erschließungsstatus fehlt je nach Fach bei
  61 % (Chemie) bis 96 % (Politik) der Materialien.

Offen: Die Wurzel „Schulfächer“ steht in den Schulfach-Kacheln als eigene Zeile (284
Materialien). `schule-erschliessung` kann Werte außerhalb des Vokabulars nicht
ausschließen (profile verträgt `vocab` nicht).
