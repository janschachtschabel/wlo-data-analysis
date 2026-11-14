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
6. **Soll und Ist.** Zielwerte je Kachel.
7. **WLO gegen OERSI.** Mitgelieferte Gegenüberstellung.

## Tests

`tests/test_growth.py`, `tests/test_highlights.py`, `tests/test_builtin.py` (Kontexte),
`trend.spec.ts`, `overview.spec.ts`, Durchstich.
