# Paket 16: Berichte selbst bauen

**Anlass:** Jan (2026-11-14, 19:39 Berlin): „Kann man so ein template wie die oer statistik tatsächlich mit den bord mittel bauen oder war dies nur extern möglich? prüfe die gesamte app nochmal auf sinnvolle funktionale ergänzungen zu ansichten, features, der aufbereitung von datenproblemen der verbesserungen des assistenten usw.“

**Befund:** Die mitgelieferte OER-Statistik hat ein Skript außerhalb des Repos als JSON erzeugt. Der Baukasten (`frontend/src/app/library/spec-form.ts`) kennt Text, Tabelle und Grafik; für den Abschnitt `matrix` zeigt er nur den Titel und verweist auf die Spezifikation. Der Assistent hat `propose_view` und `propose_dashboard`, kein Werkzeug für Templates. Die Engine und das Modell (`backend/wda/store/specs.py`: `MatrixSection`, `MatrixRows`, `RowSection`, `QueryBlock`, `ManualBlock`, `OersiBlock`) können alles, was die Statistik braucht.

**Goal:** Ein Bericht wie die OER-Statistik entsteht ganz in der App: von Hand im Baukasten, oder als Vorschlag des Assistenten, auch aus einer angehängten Vorlage. Angelegt wird weiterhin nur durch Klick des Admins.

**Status:** Plan. Die Reihenfolge der Pakete 16 bis 19 liegt bei Jan zur Wahl; gebaut wird nach seiner Antwort.

## Entscheidungen (Vorschlag)

- **Editor für die gegliederte Tabelle** als eigene Komponente `library/matrix-form.ts`, eingehängt in den Baukasten statt des Verweises auf die Spezifikation. Was der Editor nicht kennt, bleibt im JSON unberührt (wie heute beim Baukasten).
  - Zeilen: Feld wählen; Zeilengruppen anlegen, benennen, ordnen; Werte je Gruppe über dieselbe Wertesuche wie im Explorer (Name und URI); „Rest“ und „ohne Wert“ als Schalter; Zwischensummen an oder aus.
  - Spalten (`series`): Name, Zusatzfilter oder Formel über andere Spalten, Anzeige (`show`).
  - Blöcke: je Block Name und Quelle. Abfrage: gemeinsame Filter des Blocks. OERSI: Zuordnung der Spalten (`total`, `licensed`, Lizenzliste). Handeintrag: Raster zum Ausfüllen (Zeilen × Spalten des Blocks, Summen je Gruppe), dazu Stand und Anmerkung.
- **Platzhalter per Klick:** Der Text-Abschnitt bekommt eine Liste der auflösbaren Zahlen aus `POST /api/check` oder der Vorschau (Summe, Zelle, Datum, Instanz); ein Klick fügt `{{…}}` an der Schreibmarke ein.
- **Assistent: `propose_template`.** Drittes Vorschlagswerkzeug. Eingabe: Titel, Abschnitte (Text, Tabelle oder Grafik mit Abfrage, gegliederte Tabelle in vereinfachter Form: Feld, Gruppen mit Wertenamen, Spalten, Blöcke nur vom Typ Abfrage). Das Werkzeug löst Namen in Werte auf, prüft über dieselbe Validierung wie `POST /api/check`, rechnet die Vorschau und hängt den Bericht an die Karte (Grenze wie beim Dashboard, `MAX_PREVIEW_CHARS`). Handeinträge und OERSI-Blöcke schlägt der Assistent nicht vor; er nennt im Text, welche Spalten der Vorlage so zu ergänzen wären. Karte im Chat: „Vorschau, noch nicht angelegt“, Knopf „Template anlegen“, danach „Template öffnen“ und „bearbeiten“.
- **Leitfaden:** Mit angehängter Vorlage, die ein Bericht ist (mehrere Tabellen, Fließtext), ein Template vorschlagen statt einzelner Kennzahlen; Zahlen der Vorlage nie übernehmen.
- **Nicht in diesem Paket:** Word-Export, Kommentar zum Bericht durch den Assistenten, Vorlage für die ganze Unterhaltung. Stehen im Dokument „WLO-Daten-Dashboard: sinnvolle Ergänzungen“.

## Review Focus

1. Kann der Assistent über das neue Werkzeug etwas speichern oder mehr als Aggregate sehen?
2. Validierung: Erzeugt der Editor nur Spezifikationen, die `TemplateSpec` annimmt? Bleibt Unbekanntes im JSON erhalten (Hin- und Rückweg ohne Verlust, geprüft an `builtin/oer-statistik.json`)?
3. Handeinträge: Schlüssel der Zellen (Wert der Zeile), Zahlenformat, leere Zellen.
4. Größe und Laufzeit der Vorschau eines Templates im Chat.
5. Bedienung mit Tastatur, schmale Fenster.

## Tasks

1. Test zuerst: Hin- und Rückweg der OER-Statistik durch die Editor-Logik (`library/matrix-edit.ts`, reine Funktionen) ohne Verlust; dann die Funktionen (Gruppe anlegen, Wert verschieben, Block anlegen, Handzelle setzen).
2. `library/matrix-form.ts` und Einbau in `spec-form.ts`; Durchstich: OER-Statistik öffnen, Gruppe umbenennen, Handeintrag setzen, Vorschau, speichern.
3. Platzhalterliste im Text-Abschnitt.
4. `llm/tools.py`: `propose_template` mit Tests (`tests/test_llm.py`: Vorschlag trägt Bericht, nichts gespeichert, ungültige Eingabe kommt als Fehlertext zurück); Leitfaden.
5. Karte im Chat (`assistant/assistant.ts`, `chat.ts`), Durchstich mit gespieltem Modell.
6. README, Review durch frischen Reviewer, Befunde, „Stand nach Review“, Probe mit echtem Modell auf Jans Rechner (Vorlage: eine Statistik als docx und als PDF).
