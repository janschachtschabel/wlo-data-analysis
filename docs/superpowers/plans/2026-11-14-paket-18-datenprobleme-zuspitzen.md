# Paket 18: Datenprobleme zuspitzen

## Ziel

Die Qualitätsseiten zeigen viele Tabellen, aber nicht, wo man anfangen soll und welche
Materialien hinter einer Zahl stehen. Dieses Paket macht aus Zahlen Arbeitslisten, ohne die
Regel zu brechen, dass nur Auswertungen nach außen gehen.

## Umfang

1. **Von der Zelle in den Explorer.** Tabellen in Berichten tragen `drill` (Instanz, Filter,
   Zeilenfeld, Gruppen, Spaltenfilter). Die Oberfläche macht jede gezählte Zelle zum Link in
   den Explorer mit genau dieser Abfrage. Keine Links bei Füllgraden, Vokabularabgleich,
   Größenklassen, Lückenliste, Abzug, Profil und Rangliste, weil die Zelle dort keine einfache
   Filterabfrage ist.
2. **Rangliste der größten Datenprobleme.** Neue Abfrageform `problems` (Felder, `top`): führt
   Füllgrade („ohne Angabe“) und Vokabularabgleich („Wert außerhalb der Werteliste“) zu einer
   nach Größe geordneten Tabelle zusammen. Gerechnet wird mit den beiden vorhandenen
   Auswertungen; Felder im Filter oder ohne Werteliste werden nicht verglichen.
3. **Steckbrief je Quelle.** Neues mitgeliefertes Dashboard „Steckbrief“ als Reiter unter
   Qualität: Rangliste, Füllgrade, Fächer, Bildungsstufen, Materialarten, Lizenzen, Sprachen,
   Bezugsquellen, Zufluss, häufigste Schlagwörter. Mit dem Kontextschalter gilt alles für eine
   Quelle oder Bezugsquelle.
4. **Pauschal vergebene Schlagwörter.** `topics.min_share`: nur was mindestens diesen Anteil der
   Materialien einer Zeile trägt. Kachel je Quelle im Dashboard „Datenprobleme“.
5. **Balkendiagramm.** Anzahl und Anteil in Prozent teilen sich keine Achse; der Anteil steht
   in der Tabelle.
6. **Link in die Suche des Repositoriums.** Aus dem Explorer zur Trefferliste in der
   Redaktionsumgebung, sobald die Form des Links an der echten Instanz gemessen ist.

## Tests

- `tests/test_drill.py`: jede Zelle lässt sich über `/api/query` aus `drill` nachzählen.
- `tests/test_problems.py`: Rangliste gegen Füllgrade und Vokabularabgleich, `top`, Filter,
  Felder ohne Werteliste, Unverträglichkeiten.
- `tests/test_topics.py`: `min_share`.
- `tests/test_builtin.py`: Rangliste und pauschale Schlagwörter laufen, Steckbrief läuft für
  alles und für eine Quelle mit den Zahlen dieser Quelle.
- Oberfläche: `drill.spec.ts`, `options.spec.ts`; Durchstich öffnet Steckbrief und Rangliste
  und folgt einer Zelle in den Explorer.
