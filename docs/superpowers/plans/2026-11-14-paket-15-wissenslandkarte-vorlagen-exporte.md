# Paket 15: Wissenslandkarte, Vorlagen im Chat, Exporte

**Anlass:** Jan (2026-11-14, 13:21 Berlin): „verdoppel die anzahl von schritten und runden . macht es sinn einen support für pdf oder dokument eingaben im chat zu ermöglichen damit man vorlagen eingeben kann - soll aber nichts gespeichert werden. nur umgewandelt und verwendet werden. spannend wären auch thematische datenauswertungen - basierend auf keywords mit fach x bildungsstufenmatrix. man könnte je fach bildungsstufen kombination die top themen (von doppelungen bereinigte keywords) auswerten und eine wissenslandkarte anbieten. vielleicht in jede zelle die top 10. oder du findest eine bessere option für eine wissenslandkarte. prüfe was noch sinnvolle datenauswertungen sein können. können ermittelte übersichten, grafiken und zahlen auch sinnvoll exportiert werden um sie weiter zu verwenden? z.b. png, csv, json, xls oder ähnliches?“

**Goal:** Der Chat darf doppelt so lange arbeiten und nimmt Dokumente als Vorlage, ohne sie zu speichern. Je Fach und Bildungsstufe zeigt eine Wissenslandkarte die Themen des Bestands. Jede Tabelle und jede Grafik lässt sich mitnehmen.

## Messung gegen WLO (Jans Rechner)

- `cclom:general_keyword` ist je Zelle zählbar (0,2 bis 0,4 s je Abfrage). Die rohen zehn häufigsten Schlagwörter bestehen in videolastigen Zellen fast nur aus Füllwörtern („einfach erklärt“, „Nachhilfe“) und aus Sätzen, die ein Kanal an alle seine Materialien hängt.
- `ccm:curriculum` liefert echte Lehrplanthemen, deckt aber nur rund 8 % des Bestands.
- Erster Lauf der Karte (Stand 73a4afa): 5 Kacheln, 393 Abfragen, 8,2 s. Redaktionell gepflegte Zellen (Deutsch × Primarstufe, bpb) sind gut; in YouTube-lastigen Zellen blieben Bruchstücke, Kanalnamen und Stimmungswörter.

## Entscheidungen

- Grenzen des Chats: 16 Runden, 32 Werkzeugaufrufe, 8 Minuten; Gedächtnis je Frage 8000 Zeichen.
- Neue Abfrageart `topics` in der Engine statt einer Sonderseite: je Zelle eine Facettenzählung auf dem Themenfeld, höchstens 480 Zellen. So gelten Kontext, Filter, Berichte, Export und der Assistent ohne Zusatz. Es fließen nur Zählungen, kein Material.
- Statt „die zehn häufigsten“ ist die Vorgabe „die zehn typischsten“: Anteil in der Zelle geteilt durch Anteil im Gesamtbestand (mindestens 1,2), gewichtet mit der Menge. Angezeigt wird nach Menge sortiert. `rank: "frequent"` zählt schlicht.
- Bereinigung freier Schlagwörter (abschaltbar mit `stopwords: false`): Schreibweisen zusammenführen, Füllwortliste, Namen von Zeile und Spalte, Wörter bis zwei Zeichen, Jahreszahlen, Schlagwortblöcke eines Kanals (vier oder mehr Werte mit fast gleicher Zahl ab 20). Jede Bereinigung steht als Hinweis am Ergebnis.
- Themen aus einem Vokabular: nur Werte, die das Vokabular kennt, und nicht das Fach der Zeile selbst.
- Dokumente: `POST /api/assistant/document` wandelt PDF, docx und Textformate in Text um und gibt ihn zurück; nichts wird abgelegt. Der Text reist einmal mit der nächsten Frage und steht zwischen Marken als Material, nicht als Anweisung. Grenzen: 8 MB, 60 Seiten, 24.000 Zeichen. docx wird ohne Fremdbibliothek gelesen, DOCTYPE und ENTITY werden abgewiesen.
- Exporte: Excel (`xlsx`, eigener kleiner Schreiber ohne Abhängigkeit) am Lauf; „CSV herunterladen“ je Tabelle und „PNG herunterladen“ je Grafik überall in der Oberfläche, im Browser aus dem Gezeigten erzeugt. CSV schützt vor Formeln (`=`, `+`, `-`, `@` am Anfang).

## Review Focus

1. Dokumente: Wird wirklich nichts gespeichert oder protokolliert? Grenzen bei großen, kaputten oder bösartigen Dateien (Zip-Bombe im docx, XML-Entitäten, sehr viele Seiten)? Kann der Text der Vorlage das Modell zu etwas bringen, das es sonst nicht darf?
2. `topics`: gelangt mehr als Aggregate nach außen? Abfragezahl und Grenzen, Verhalten bei Teilausfällen, Kontext (`scope`), Richtigkeit von Anteil und Typik.
3. Bereinigung: fallen echte Themen weg (Blockregel, kurze Wörter, Namensregel)?
4. XLSX und CSV: Formeln, Sonderzeichen, Blattnamen, sehr große Tabellen.
5. Grenzen des Chats: Kosten und Laufzeit, Abbruch.

## Tasks

1. `llm/assistant.py`, `api/assistant.py`, `assistant/chat.ts` (Grenzen).
2. `engine/spec.py`, `engine/topics.py`, `engine/engine.py`, Berichtsformate, `builtin/themen.json`, Reiter, `tests/test_topics.py`.
3. `report/xlsx.py`, `shared/download.ts`, Knöpfe an Tabelle und Grafik.
4. `llm/document.py`, Route, Anhängen im Chat, `tests/test_document.py`.
5. Review, Befunde, Probe auf Jans Rechner.

## Weitere sinnvolle Auswertungen (aus der Messung)

- Abhängigkeit von einer Quelle je Fach (Anteil der größten Quelle).
- Offenheit (Lizenzkategorie) je Fach × Bildungsstufe.
- Füllgrade je Zuflussjahr: wird neu Hinzugekommenes besser erschlossen?
- Sprachmix je Fach, Materialart-Mix je Bildungsstufe.

## Stand nach Review

(folgt)
