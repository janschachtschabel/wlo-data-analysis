# Paket 13: Bedienung, Beitrag der Quellen, Kontext ohne Fehlkacheln

**Anlass:** Jan (2026-11-14, 11:58 Berlin), mit drei Bildschirmfotos: „schraffierte flächen sind nicht angenehm zu sehen. vielleicht anders lösen - vielleicht die farbe ohne schraffur? interessant wäre auch wie welche quelle zum datenbestand beiträgt - vielleicht beim explorer - also mit fach und bildungsstufen . prüfe auch ob die universitäts fächer dabei sind - das ist das 2. vocab in taxonid - es gibts auch als eigenes feld taxonid_university. Wie funktioniert das template anlegen? hab was eingegeben aber nichts passiert und es gibt keine hilfen im programm. der menüpunkt assistent sollte im menü ganz links stehen und die app sollte darauf starten“. Das erste Bild zeigt im Kontext einer Bezugsquelle den Fehler „Das Zeilenfeld 'ccm:oeh_publisher_combined' steht auch in den Filtern …“.

**Goal:** Lücken ruhig darstellen, den Beitrag der Quellen im Explorer sichtbar machen, im Kontext einer Quelle keine Fehlkacheln, Anlegen von Dashboards und Templates ohne Vorwissen, Start beim Assistenten.

## Entscheidungen

- **Lücken:** rötlich hinterlegte Fläche statt Schraffur, die 0 in gedämpfter Schrift; in der Oberfläche und in den Exporten (`report/heat.py`).
- **Kontext auf dem Feld der Kachel** (`store/runner.py`, `_narrow`): einfache Zeilen behalten den Filter (Summen stimmen) und verlieren „ohne Wert“ und „Übrige“; bei `profile` und bei Spalten wird der Filter zur Auswahl (`include`); `sizes`, `profile` über das Kontextfeld und eine reine Vokabularprüfung dieses Feldes entfallen mit dem Satz „Entfällt im gewählten Kontext …“, den die Oberfläche gedämpft statt als Fehler zeigt.
- **Beitrag der Quellen:** Der Explorer bot Quelle und Bezugsquelle bisher nicht zum Aufschlüsseln an (keine Werteliste). `GET /instances/{id}/fields` liefert jetzt `facet`; dazu sechs Schnellansichten (Fach × Quelle, Hochschulfach × Quelle, Bildungsstufe × Quelle, Fach × Bezugsquelle, Fach × Bildungsstufe, Hochschulfach × Bildungsstufe).
- **Hochschulfächer:** gegen WLO gezählt, 259 Werte der Hochschulfächersystematik mit 93.869 Zuordnungen in `ccm:taxonid`, in „Abdeckung“ mit eigenen Kacheln. Das eigene Feld heißt `ccm:oeh_taxonid_university`; die öffentliche Suche nimmt es weder als Facette noch als Filter (400), auswertbar wäre es nur über den lokalen Abzug.
- **Anlegen:** Der Baukasten erklärt die drei Wege, übernimmt fertige Kacheln aus vorhandenen Dashboards und lässt den Assistenten vorschlagen; dessen Vorschlag wandert mit „In … übernehmen“ in die geöffnete Bearbeitung (`Chat.target`). Gespeichert wird weiter erst mit „Speichern“.
- **Start:** Menü beginnt mit „Assistent“, `/` führt dorthin; der Explorer liegt unter `/explorer`, alte Links `/?q=…` gelten weiter.

## Review Focus

1. `_narrow`: stimmen Zahlen und Summen im Kontext für jede Abfrageform, auch bei mehreren Kontextfiltern, Präfix-Feldern, `fill` je Zeile, Reihen mit Formeln? Kann eine Kachel falsche Zahlen zeigen statt zu entfallen?
2. Umleitung `/` und alte Links, Anmeldung mit `weiter=`, Schnellansichten bei Instanzen ohne diese Felder.
3. Übernehmen aus dem Chat: Kennungen, Layout, Template-Abschnitte, ungespeicherte Änderungen, Ziel nach Verlassen der Bearbeitung.
4. `facet` in der Feldliste: Felder, die sich dann doch nicht aufschlüsseln lassen.

## Tasks

1. Schraffur ersetzen (Oberfläche, Export, Texte).
2. `_narrow` mit Tests gegen alle mitgelieferten Dashboards (`tests/test_run_scope.py`).
3. `facet`, Schnellansichten, Route `/explorer`, Menü.
4. Baukasten: Hilfe, Quellen-Dashboards, Übergabe an den Assistenten und zurück; Durchstich.
5. Review, Befunde, Probe auf Jans Rechner.
