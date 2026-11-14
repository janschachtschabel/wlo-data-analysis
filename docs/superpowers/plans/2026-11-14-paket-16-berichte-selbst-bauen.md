# Paket 16: Berichte selbst bauen

**Anlass:** Jan (2026-11-14, 19:39 Berlin): „Kann man so ein template wie die oer statistik tatsächlich mit den bord mittel bauen oder war dies nur extern möglich? prüfe die gesamte app nochmal auf sinnvolle funktionale ergänzungen zu ansichten, features, der aufbereitung von datenproblemen der verbesserungen des assistenten usw.“

**Befund:** Die mitgelieferte OER-Statistik hat ein Skript außerhalb des Repos als JSON erzeugt. Der Baukasten (`frontend/src/app/library/spec-form.ts`) kennt Text, Tabelle und Grafik; für den Abschnitt `matrix` zeigt er nur den Titel und verweist auf die Spezifikation. Der Assistent hat `propose_view` und `propose_dashboard`, kein Werkzeug für Templates. Die Engine und das Modell (`backend/wda/store/specs.py`: `MatrixSection`, `MatrixRows`, `RowSection`, `QueryBlock`, `ManualBlock`, `OersiBlock`) können alles, was die Statistik braucht.

**Goal:** Ein Bericht wie die OER-Statistik entsteht ganz in der App: von Hand im Baukasten, oder als Vorschlag des Assistenten, auch aus einer angehängten Vorlage. Angelegt wird weiterhin nur durch Klick des Admins.

**Status:** Jan hat das Paket am 2026-11-14 (20:18 Berlin) gewählt; in Arbeit.

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

## Nebenbefunde aus der Liveprobe (Jans Rechner, e6ad250)

- `POST /api/check` meldet ein Template als gültig, dessen Vorschau scheitert, weil weder am Template noch beim Lauf eine Instanz steht. Die Prüfung soll das als Hinweis nennen. Gehört in Task 2.
- Ein neu angelegtes Template hat keinen Abschnitt; der Baukasten soll dafür einen Einstieg bieten (leere gegliederte Tabelle, Text, Tabelle).
- Der Leitfaden des Assistenten erklärt Gruppen (`rows.groups`) nicht; im Versuch baute er 41 Fächer nach Alphabet statt der verlangten Fächergruppen. Gehört in Task 4.
- Geprüft und kein Fehler: „ohne Wert“ zeigt in „A: OER“ für Primarstufe und Sekundarstufe I dieselbe Zahl (2.090). 1.994 davon sind Klexikon-Artikel ohne Fach, die beide Stufen tragen.


## Stand nach Review (2026-11-14)

Umgesetzt in drei Commits (Baukasten, `propose_template`, Befunde). Frischer Reviewer, ohne Schreibrecht im Repo: keine kritischen Befunde. 56 schiefe Eingaben an `propose_template` kamen alle als Fehlertext zurück, Filter auf gesperrten Feldern werden in Spalten, Blöcken und Tabellen abgewiesen, gespeichert wird nichts, Text-HTML ist maskiert, der Hin- und Rückweg der OER-Statistik ist bytegleich.

Behoben:

1. Last: Ein Vorschlag mit 12 gegliederten Tabellen löste 5.832 Abfragen aus. Jetzt höchstens 3 gegliederte Tabellen je Bericht, 12 Spalten, 6 Blöcke, 300 Werte je Gruppe.
2. Leitfaden nannte `"other": true` bei `rows.groups`; das gibt es so nicht. Satz entfernt, Platzhalter `cell` und `run.name` ergänzt.
3. Jede Änderung im Baukasten lud die Wertenamen jeder Tabelle neu. Jetzt nur bei Wechsel von Feld oder Instanz.
4. Enter in einem Feld des Editors speicherte das Template. Enter übernimmt jetzt nur den Wert.
5. `tidy` warf Handeinträge weg, die unter der aufgelösten Fassung eines Wertes stehen. Zeilen fallen nur noch weg, wenn ihr Wert entfernt wird.
6. Der Editor erzeugt kein leeres `show`, keine leeren OERSI-Zählungen, keine Kennung `max`, `min`, `round`, höchstens 40 Gruppen.
7. Platzhalterwahl je Text-Abschnitt statt einer gemeinsamen; eine veraltete Wahl fällt auf Gesamtzeile und erste Spalte.
8. Rückfrage beim Feldwechsel auch, wenn nur Mitzähler betroffen sind.
9. Eine eigene Reihenfolge in `show` bleibt.
10. Werkzeug: Fehler statt stiller Änderung (zu viele Werte, Filter und Formel zugleich, `missing` kein Wahrheitswert, doppelte Gruppe); offene Platzhalter gehen an das Modell zurück.
11. Namen der Felder je Tabelle eindeutig, schmalere feste Spalte im Raster.
12. `ident` in Python wie in der Oberfläche (Akzente).

Dazu aus der CI: Die Filterleiste startete ihre verzögerte Wertesuche noch nach dem Abbau.

Offen gelassen: Fokus nach dem Umordnen von Gruppen, Spalten und Blöcken; wiederholte Meldung „Keine Zahl“ wird nicht erneut vorgelesen.

## Liveprobe (Jans Rechner, 344ef13, echtes Modell, WLO Produktion)

- Prüfung: OER-Statistik gültig ohne Hinweis; ohne Instanz ein Hinweis an `sections.1`.
- Frage nach einem Bericht mit Fächergruppen: 29,9 s, 8 Schritte, ein Vorschlag `template` mit Vorschau (Text, gegliederte Tabelle mit 5 Gruppen, Spalten gesamt, oer, anteil, Blöcke Primarstufe, Sek I, Sek II), alle Platzhalter gefüllt, nichts gespeichert. Angelegt, gerechnet (6 Teilabfragen ohne Fehler), als xlsx exportiert, gelöscht.
- Word-Vorlage: 21,0 s, 10 Schritte, Gliederung nachgebaut, keine Zahl der Vorlage als gezählt genannt. PDF-Vorlage nicht geprüft (kein Werkzeug zum Erzeugen auf dem Rechner).
- Auffällig: Das Modell nahm gleichnamige Hochschulfächer und Fächer außerhalb der Vorlage dazu und beschrieb die Tabelle falsch. Leitfaden dazu ergänzt. Offen: Der Hinweis `values_ungrouped` nennt Werte als Adressen statt mit Namen; Sammelwerte wie „Allgemein“ prägen eine Gruppe „Sonstige“.
