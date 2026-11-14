# Paket 12: Kontext, Datenströme der Quellen, Chatfenster, KI über die Umgebung

**Anlass:** Jan (2026-11-14, 10:41 Berlin), mit Bildschirmfoto eines Fehlers des Assistenten: „die app sollte mit explorer und füllgrade ansicht starten. das die llm einstellungen in der app sind ist vielleicht ungünstig - besser wäre es gewesen dies als env variablen zu lösen damit man es nicht sichtbar machen muss. LLM Abfragen gehen nicht. Bei Datenproblemen sollten der Kontext betrachtet werden - Gesamtbestand oder nach Quellen. Bei den Quellen kannst Du noch mehr Wissen aus Quellenpanel nachnutzen um Probleme sichtbar zu machen. Bei den Quellen mehr in Datenströmen denken und nicht in Einzelinhalten. ist das anlegen von filtern und templates benutzerfreundlich und nativ? die ki funktion kann nur zahlen aus geben oder auch automatisiert passende grafiken oder abbildungen erzeugen? wäre ein angemesseres chatfenster besser?“

**Goal:** Der Assistent antwortet mit dem echten Modell. Die App beginnt mit Füllgraden. Jede Auswertung zur Qualität lässt sich auf eine Quelle einengen. Der Reiter „Quellen“ zeigt Quellen als Ströme (Bestand, Zufluss, Pflege). Der Assistent ist ein Chatfenster neben jeder Ansicht. Filter lassen sich ohne JSON ablegen und anwenden.

## Entscheidungen

- **LLM-Fehler:** `gpt-6-luna` nimmt Werkzeuge über `/v1/chat/completions` nur mit `reasoning_effort: "none"`. Der Client passt sich an die Fehlermeldung an (erst `none`, wenn die Meldung es nennt, sonst ohne den Parameter) und meldet, wenn nichts hilft. `WDA_LLM_EFFORT` (`auto`, `off`, `low`, `medium`, `high`).
- **KI über die Umgebung:** Ohne `WDA_LLM_SETTINGS_UI=on` gilt allein die Umgebung; die Seite „Einstellungen“ zeigt nur an und prüft die Verbindung, Schreiben antwortet 409.
- **Start:** Der Explorer zeigt ohne Zeilenfeld die Füllgrade der wichtigsten Felder; „Qualität“ beginnt mit „Füllgrade“.
- **Kontext (`scope`):** bis zu drei Filter am Lauf, die vor jede Abfrage treten. Nicht Teil der Definition, nie gespeichert (422 bei `save=true` oder `wait=false`), damit Verlauf und Zeitreihen beim Gesamtbestand bleiben. Eine Kachel mit eigenem Filter auf dem Feld meldet das als Fehler der Kachel; Abfragen aus dem Abzug und Abrufe von außen (OERSI) ebenso.
- **Zeit:** Das Repositorium kennt nur „seit“. Neuer Filterwert `{"since": "30d"|"12m"|"5y"}` nur für `virtual:time_after_created` und `virtual:time_after_modified`, aufgelöst zu Millisekunden des Tagesbeginns in UTC (einen Tag lang dieselbe Abfrage, der Zwischenspeicher greift). Zeiträume sind Differenzen zweier Spalten; `hidden: true` verbirgt Hilfsspalten.
- **Datenströme:** drei Kacheln oben im Reiter „Quellen“: Bestand, neu in 30 Tagen und 12 Monaten, Zufluss in Prozent, seit 12 Monaten unberührt (je Quelle und je Bezugsquelle), dazu das Alter des Bestands je Quelle als gestapelte Balken. Gestapelt wird auch, wenn alle Spalten berechnet sind.
- **Chat:** Die Unterhaltung liegt in einem Dienst (`assistant/chat.ts`), nicht in der Ansicht. „Chat“ im Kopf öffnet sie als Fenster neben der Seite, mit der gezeigten Auswertung als Bezug; „Assistent“ zeigt dieselbe in voller Breite. Schritte eingeklappt, Vorschläge zum Weiterfragen. Nichts wird gespeichert; nach dem Neuladen ist sie weg.
- **Filter anlegen:** An der Filterleiste des Explorers „Diese Filter speichern“ und „Gespeicherter Filter anwenden“. Vorher ging ein Filter nur über Import oder JSON.
- **Neuere mitgelieferte Fassung:** Die Reiter unter „Qualität“ melden, wenn die installierte Auswertung von der mitgelieferten abweicht, und bieten an, sie zu übernehmen.

## Review Focus

1. `scope`: Kann ein Lauf mit Kontext doch gespeichert werden oder in Verlauf, Zeitreihen, Aufträge geraten? Stimmen die Zahlen gegen die Daten, auch bei Matrix-Abschnitten, Kennzahl-Kacheln mit gespeicherten Filtern, `profile`, `fill`, `gaps`?
2. `since`: Grenzfälle (Monatsende, Schaltjahr, sehr große Zahlen), Zeitzone, Zwischenspeicher, Fehlertexte; darf der Wert an Stellen auftauchen, die ihn nicht verstehen (Zeilenwerte, Gruppen, gespeicherte Filter, Import/Export, Bericht, Assistent)?
3. `hidden`: Prozent, Gesamtzeile, Herkunft, Vergleich mit früherem Lauf, Zeitreihen-Punkte, Exporte.
4. Sperre der KI-Einstellungen: Gibt es einen Weg, bei gesperrter Oberfläche Adresse, Modell oder Schlüssel zu ändern oder den Schlüssel zu lesen? Fehlerbehandlung des `reasoning_effort`.
5. Chat: laufende Antwort bei Seitenwechsel, Abmelden und abgelaufener Sitzung; Bezug auf die falsche Auswertung; schmale Fenster; Tastatur und Screenreader.
6. Sicherheitsvorgabe: Rohdaten aus dem Zwischenspeicher bleiben von außen unzugänglich.

## Tasks

1. LLM-Client, `WDA_LLM_EFFORT`, `WDA_LLM_SETTINGS_UI`, Einstellungsseite nur lesend (Commits 5ccb459, d63f1ac).
2. Start mit Füllgraden (5ccb459).
3. `scope` in `api/runs.py`, `store/runner.py`; Auswahl in `quality/quality.ts`; Tests `tests/test_run_scope.py` (72bc016).
4. `since`, `hidden`, Kacheln in `builtin/quellen.json`, Tests `tests/test_since.py`; Chat-Dienst und Fenster; Hinweis auf neuere Fassung (a244eea).
5. Filter speichern und anwenden im Explorer; Leitfaden des Assistenten; README.
6. Review durch frischen Reviewer, Befunde beheben, Probe auf Jans Rechner.

## Probe mit echtem Modell (Jans Rechner, Stand d63f1ac)

- Verbindungstest 200 in 1,6 s. „Wieviele Inhalte mit Mathematik gibt es?“: 28.874, Gegenprobe über `/api/query` gleich. „Zeig mir als Grafik …“: Balkengrafik als Vorschlag, alle genannten Zahlen stimmen.
- Auffällig: „Mathematik“ einmal mit, einmal ohne Hochschulfächer; ein gleichnamiger Wert der Hochschulfächersystematik (`n105`) fehlte im Filter; fünf Werkzeugaufrufe für eine einfache Zählung.
- Der Schlüssel steht dort weiter in der App (Start mit `WDA_LLM_SETTINGS_UI=on`); Jan trägt ihn selbst in `.env` ein.
