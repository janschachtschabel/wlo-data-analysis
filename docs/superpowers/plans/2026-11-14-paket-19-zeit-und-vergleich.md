# Paket 19: Zeit und Vergleich

## Ziel

Die App zeigt bisher vor allem den Stand von heute. Dieses Paket macht Veränderung sichtbar und
Vergleiche bequem, weiter allein aus Zählungen.

## Umfang

1. **Zuwachs je Monat.** Abfrageform `growth` (Monate, `by` created oder modified, `stock`): je
   Monatsanfang eine Zählung „seither angelegt“, der Monat ist die Differenz. Kachel im Steckbrief.
2. **Kontext auch für Fach und Bildungsstufe.** Der Kontextschalter der Qualitätsseiten kennt
   neben Quelle und Bezugsquelle auch Fach und Bildungsstufe. Kacheln, die genau dieses Feld
   vergleichen, entfallen mit einem Satz.
3. **Vergleich zweier Instanzen.** Auf der Seite eines gespeicherten Laufs: „Vergleichen mit
   einer anderen Instanz“ zählt dieselbe Auswertung dort, speichert den Lauf und zeigt die
   Abweichung hinter jeder Zahl (vorhandener Vergleich zweier Läufe).
4. **„Was ist auffällig?“** `GET /api/highlights`: die größten Veränderungen zwischen den beiden
   jüngsten vergleichbaren Läufen jeder Auswertung (Zählungen ab 10 und 10 %, Anteile ab 5
   Prozentpunkten, höchstens drei je Auswertung). Seite „Auffällig“ in der Navigation.
5. **Trend in Kacheln.** `GET /api/definitions/{id}/trends`: je Kachel die Gesamtzahl über die
   letzten Läufe; die Kachel zeigt eine kleine Linie mit Stand und Veränderung.
6. **Soll und Ist.** Die Darstellung trägt einen Zielwert (`view.options.target`, wahlweise
   `target_column` und `target_mode` min oder max). Der Bericht sagt in einem Hinweis, welche
   Zeilen ihn erreichen (damit steht es in jedem Export); die Tabelle markiert verfehlte Zellen.
   Im Formular für Kacheln und Abschnitte gibt es das Feld „Zielwert (mindestens)“.
7. **WLO gegen OERSI.** Mitgelieferter Bericht „WLO und OERSI im Vergleich“: Hochschulmaterialien
   nach Fächergruppe, je Bestand, offene Lizenzen und OER-Anteil, WLO und OERSI nebeneinander.

## Tests

`tests/test_growth.py`, `tests/test_highlights.py`, `tests/test_builtin.py` (Kontexte),
`tests/test_drill.py` (Zielwert), `trend.spec.ts`, `overview.spec.ts`, Durchstich.

## Stand nach Review

Ein unabhängiger Durchgang fand 13 Befunde, davon 4 wichtige. Behoben:

- „Auffällig“ meldete den normalen Zufluss des laufenden Monats: Tabellen mit Zuwachs je Monat
  bleiben dort außen vor.
- Zeilen, die verschwinden oder neu auftauchen, fehlten: Sie zählen jetzt im jeweils anderen
  Lauf als 0 (nur in Blöcken, die beide Läufe haben).
- Die Verlaufslinie hieß „Gesamt“, zeigte bei mehreren Spalten aber nur die erste: Sie nennt
  jetzt die Spalte. Sie erscheint nur noch über Läufen der Reihe und endet am gezeigten Lauf.
- Eine späte Antwort konnte den Verlauf einer anderen Auswertung zeigen; „einer anderen
  Instanz“ bot die eigene an.
- Zielwert: unbekannte Spalte oder Richtung ergibt einen Hinweis statt einer stillen Annahme;
  „höchstens“ bei einer Gesamtzahl sagt „zu viel“; der Zielwert steht ungerundet da; NaN und
  Unendlich sind kein Ziel; der Server nennt die verfehlten Zeilen, die Oberfläche markiert
  genau diese; eine Kachel, die die Darstellung der Kennzahl übernimmt, bekommt keinen
  eigenen Zielwert untergeschoben.
- Zuwachs: `stock` mit `by: modified` wird abgelehnt.

Bewusst offen: Materialien mit Anlagedatum in der Zukunft zählen im laufenden Monat mit; bei
gekürzten Tabellen (top N) kann eine Zeile aus der Tabelle fallen, ohne verschwunden zu sein,
und erscheint dann als „auf 0“.
