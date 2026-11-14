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
- Dokumente: `POST /api/assistant/document` wandelt die Datei um und gibt den Text zurück; nichts wird abgelegt. Der Text reist einmal mit der nächsten Frage und steht zwischen Marken als Material, nicht als Anweisung. Grenzen: 8 MB, 60 Seiten, 32.000 Zeichen.
- Nachtrag (Jan, 13:54 Berlin: „das ki modell sollte die struktur aus dem dokument bekommen - also z.b. pdf oder docx oder ähnliches zu markdown“): Die Umwandlung liefert Markdown statt reinem Text. Word: Überschriften aus Formatvorlage oder Gliederungsebene, Listen mit Einzug (nummeriert laut `numbering.xml`), Fettdruck, Tabellen. PDF mit `pdfplumber` statt `pypdf`: Tabellen nach ihren Linien, Überschriften nach Schriftgröße (bis drei Stufen) und fetten Einzelzeilen, Aufzählungen nach ihrem Zeichen; das ist eine Schätzung, der Leitfaden sagt es dem Modell. Dazu Excel (eine Tabelle je Blatt, höchstens 12 Blätter, 200 Zeilen, 30 Spalten) und PowerPoint (ein Abschnitt je Folie). CSV wird zur Tabelle. Office-Dateien werden ohne Fremdbibliothek gelesen; entpackt höchstens 30 MB, DOCTYPE und ENTITY werden abgewiesen.
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

Review durch einen frischen Reviewer (Stand e632026, Dokumente an 800cb40). Nichts Kritisches: kein Weg, auf dem Rohdaten nach außen gehen, die Vorlage gespeichert wird oder der Assistent selbst speichert. Zehn wichtige und neun kleine Befunde.

**Behoben**

- W1 Dateien über etwa 4,5 MB scheiterten an der allgemeinen Grenze für Anfragen: eigene Grenze für `/api/assistant/document` (12 MB für 8 MB Datei als Base64).
- W2 Kaputte Dateien gaben 500: Jeder Fehler eines fremden Lesers wird zur schlichten Meldung (422), ohne Protokoll.
- W3 pdfminer schrieb Bruchstücke des Inhalts ins Log: Die Logger von `pdfminer` und `pdfplumber` sind stumm; Test mit `caplog`.
- W4 Winzige Dateien konnten den Server minutenlang binden: PDF und Office-Dateien laufen in einem eigenen Prozess mit 20 Sekunden und 1,5 GB (Speichergrenze nur unter Linux), höchstens zwei zugleich (sonst 429). Entpackt höchstens 12 MB, Tabellenzeilen nur direkt und höchstens 200, Tabellensuche im PDF nur bis 4000 Linien je Seite.
- W5 Blockregel: gesucht wird jetzt in allen Werten der Zelle (auch Füllwörtern), ein Block braucht fünf Werte, die je mindestens ein Zehntel der Zelle tragen. Vier gleich große echte Themen bleiben.
- W6 Namensregel: nur noch der Name, seine Wörter und Kurzformen des ganzen Namens („Mathe“); „Physikdidaktik“, „Deutschland“, „Mathematikgeschichte“ bleiben. Jahreszahlen bleiben. Ein oder zwei Zeichen fallen nur weg, wenn es kein Kürzel ist (KI, EU, 3D bleiben), und nur mit `stopwords`. Aus der Füllwortliste sind fachliche Wörter heraus (Stadt, Mensch, Wort, rechnen, Ausbildung, MINT). Der Hinweis nennt alle Regeln. Die Namen von Zeile und Spalte fallen immer weg, auch mit `stopwords: false`: Der Name der Zelle ist kein Thema.
- W7 Kontext auf dem Feld der Zeilen wählt jetzt die Zeile; auf dem Themenfeld entfällt die Kachel mit einem Satz (`not_applicable`) statt als Fehler.
- W8 Excel: U+FFFE, U+FFFF und einzelne Surrogate werden entfernt.
- W9 Spaltenblöcke stehen in Excel und im CSV des Browsers vor dem Spaltennamen („WLO · Anzahl“); das CSV trägt auch die Vergleichswerte.
- W10 Die Zeit der Frage gilt auch mitten in einem Schritt (`wait_for`, mindestens 20 Sekunden je Schritt).
- G1 XML wird über expat mit Abbruch bei DOCTYPE und Entity gelesen, gleich in welcher Kodierung.
- G2 Anfragen ohne `Content-Length` werden beim Lesen gezählt (413).
- G3 Ein Thema braucht zwei Materialien in seiner häufigsten Schreibweise; der Anteil ist auf 100 % begrenzt.
- G4 Die Vorlage bleibt angehängt, bis die Anfrage angekommen ist; an der Frage steht „nur für diese Frage“.
- G5 Blattnamen: „History“ und „Übersicht“ sind vergeben, kein Rand aus Leerzeichen oder Apostroph.
- G6 Meldung je Dateiart (der Satz zum gescannten PDF nur beim PDF).
- G7 Der Dateiname der Vorlage wird auf Buchstaben, Ziffern und wenige Zeichen gekürzt.
- G8 Fällt eine Zelle der Karte aus, bleibt die Karte; ein Hinweis nennt es. Nur wenn alle ausfallen, scheitert der Lauf.
- G9 Beschriftungen für Vorleseprogramme, „keine Themen“, Datum im Dateinamen nach Ortszeit.

**Bewusst offen**

- Excel-Datumswerte kommen als Seriennummer, Text aus Word-Textfeldern ohne Trennung (G6).
- Die Endmarke der Vorlage ist fest; Doppelgänger in Unicode werden nicht ersetzt. Das Modell kann auch bei gelungener Täuschung nur zählen und vorschlagen (G7).
- Kein eigenes Budget für Abfragen am Repositorium je Frage; begrenzt sind Zeit, Runden und Schritte (W10).
- Die Vorlage reist in jeder Runde der einen Frage mit (bis 32.000 Zeichen).

## Liveprobe nach dem Review (Jans Rechner, 998ceab und f461030)

- Vorlagen: docx zu Markdown in 1,0 s (eigener Prozess unter Windows), kaputte Dateien 422. Chat mit Vorlage: 12,7 s, 9 Schritte, 2 Vorschläge, nichts gespeichert. Excel (6 Blätter) und CSV des gespeicherten Laufs gültig.
- Wissenslandkarte: 5 Kacheln, 393 Abfragen, 8,7 s. Redaktionelle Zellen gut (Biologie × Sek I 9 von 10, Deutsch × Primarstufe 8 von 10), videolastige schwach (Mathematik × Sek I 1 von 10): Sätze einzelner Kanäle rücken nach, sobald einer entfernt ist, und ihre Zahlen liegen in der Zelle nicht dicht genug für die Blockregel.
- Gemessen: Ein Schlagwort als Kriterium trifft exakt (Groß/Klein zählt), und die Facette desselben Feldes nennt dann die mitvergebenen Schlagwörter („Winston Churchill“ 671, 14 weitere an 97,6 bis 98,7 % derselben Materialien).
- Folge: Nachzählen. Für die drei vorderen Themen je Zelle (größte Zellen zuerst, höchstens 240 Zählungen je Abfrage, bis zu drei Runden) fragt die Engine die Begleiter ab. Hängen mindestens vier weitere Schlagwörter an 90 % derselben Materialien und liegen 80 % ihres eigenen Vorkommens dort, gilt das Ganze als Satz eines Kanals und fällt in allen Zellen weg (ab 50 Materialien). Der Hinweis nennt die Zahl. Abschaltbar mit `stopwords: false`.

