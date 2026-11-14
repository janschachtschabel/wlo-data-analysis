# Paket 17: Fächer unterscheidbar, Assistent genauer

**Anlass:** Jan (2026-11-14, 22:02 Berlin): „kannst du alles umsetzen wollte ich sagen“, bezogen auf die Liste der Verbesserungen. Umgesetzt wird in vier Paketen (17 bis 20). Dieses ist das erste; es behebt, was die Liveprobe von Paket 16 gezeigt hat.

**Befund aus der Liveprobe:** Gleichnamige Zeilen („Mathematik“ dreimal: Schulfach und zwei Hochschulfächer) waren in drei Proben nicht unterscheidbar. Der Antworttext des Assistenten widersprach zweimal seinem eigenen Vorschlag. Bei „Gesamtzahl“ riet er die Lesart. Der Hinweis zu nicht zugeordneten Werten nannte Adressen statt Namen.

**Goal:** Jeder Wert hat überall einen eindeutigen Namen; der Assistent prüft seinen Text gegen den Vorschlag und fragt nach, statt zu raten; Hinweise sind lesbar.

## Entscheidungen

- **Namen an der Wurzel:** `Instance.vocab_names` ordnet dem Anfang der Werte einen Kurznamen zu (WLO: `…/discipline/` → Schule, `…/hochschulfaechersystematik/` → Hochschule). `VocabStore._told_apart` hängt ihn an, wenn derselbe Name in mehr als einem Vokabular vorkommt („Mathematik (Schule)“). Bleiben Werte gleich benannt, kommt dazu, worunter sie stehen, sonst das Ende der Kennung. Der alte Name bleibt suchbar und mehrdeutig, wie bisher; gespeicherte Definitionen tragen Kennungen und ändern sich nicht. Instanzen ohne `vocab_names` bleiben unverändert.
- **Selbstprüfung:** Nach einer Antwort mit Vorschlag geht ein Aufruf ohne Werkzeuge an das Modell: der Text und, was die Vorschläge zeigen (aus dem gerechneten Bericht: Tabellen, Zeilen, Spalten, Blöcke, Hinweise). „OK“ lässt den Text, alles andere ersetzt ihn. Scheitert der Aufruf, bleibt der Text. Kein Aufruf ohne Vorschlag, in der letzten Runde oder nach Ablauf der Zeit.
- **Rückfrage:** Werkzeug `ask_choice` (Frage, zwei bis vier Antworten, höchstens eine je Antwort). Im Chat erscheinen die Antworten als Knöpfe; ein Klick stellt sie als nächste Frage.
- **Hinweise:** `values_ungrouped` nennt die Werte mit Namen.
- **Vorlage genau nehmen:** steht seit 826bfee im Leitfaden.

## Review Focus

1. Ändert die Umbenennung Ergebnisse, Schlüssel oder gespeicherte Definitionen? Trifft die Auflösung von Namen noch, was sie vorher traf?
2. Selbstprüfung: Kann sie Zahlen erfinden, Schleifen bilden, die Zeitgrenze sprengen oder mehr als Aggregate an das Modell geben?
3. `ask_choice`: Eingaben des Modells (Typen, Längen), Darstellung, Tastatur.

## Tasks

1. Tests und Umsetzung `vocab_names` und `_told_apart` (`tests/test_vocab.py`).
2. Hinweis mit Namen (`engine.py`, `tests/test_matrix.py`).
3. Selbstprüfung und `ask_choice` (`llm/assistant.py`, `llm/tools.py`, `tests/test_llm.py`), Leitfaden.
4. Knöpfe im Chat (`assistant/assistant.ts`, `chat.ts`), Durchstich.
5. README, Review, Befunde, Liveprobe.

## Stand nach Review

Ein unabhängiges Review fand keine kritischen, vier wichtige und neun kleinere Befunde. Behoben:

- Die Themenanalyse rechnet wieder mit dem Namen ohne Zusatz („Mathematik“ statt „Mathematik
  (Schule)“); das Umbenennen ändert keine Themen mehr. Dasselbe gilt für die Wertesuche.
- Die Selbstprüfung übernimmt nur noch einen berichtigten Text: alles, was mit OK beginnt, ein
  Kommentar zum Text, ein viel kürzerer Text oder einer mit neuen Zahlen lässt die Antwort stehen.
  Sie ist auf 45 Sekunden begrenzt.
- Die Fakten für die Prüfung werden je Vorschlag zeilenweise gekürzt und als gekürzt markiert, die
  Summenzeile zählt nicht als Zeile, Namen aus dem Repositorium sind einzeilig und als Daten
  gekennzeichnet.
- Die Rückfrage mit ihren Antworten bleibt im Gesprächsverlauf.
- Der Leitfaden nennt die Namen so, wie die Wertesuche sie liefert.
- Rückfrage ohne Text des Modells zeigt „Bitte wähle eine der Antworten.“; die allgemeinen
  Folgefragen entfallen dann; die gewählte Antwort ist markiert, der Fokus geht ins Eingabefeld,
  lange Antworten brechen um. Frage und Antworten werden strenger geprüft.
- Unterscheidung gleichnamiger Werte: längster Anfang gewinnt, nie zweimal derselbe Name, kein
  Zusammenstoß mit vorhandenen Namen, lineare Laufzeit.

## Liveprobe (echtes Modell, WLO Produktion, Stand 25ed657)

Was hielt: „Mathematik (Schule)“ und die Hochschulwerte sind unterscheidbar, 41 von 416 Werten
tragen einen Zusatz, der erste Abruf der Felder dauert 1,5 s. Der Assistent nimmt Schule und
Hochschule zusammen und sagt es (28.874, nachgerechnet richtig). Die Rückfrage kommt mit vier
Antworten und die Unterhaltung geht danach sinnvoll weiter. Die Selbstprüfung kostet kaum Zeit.

Was nicht hielt, und was daraufhin geändert wurde:

- Drei von neun Läufen endeten nach rund zwei Minuten mit einer Zeitüberschreitung des Modells.
  Jetzt wird ein Aufruf nach 90 Sekunden einmal wiederholt (mit wenig Nachdenken, falls mehr
  eingestellt war).
- Vorschläge gingen verloren, weil gescheiterte Versuche die Grenze von zwei Dashboards oder
  Berichten je Frage verbrauchten. Jetzt zählen nur gelungene; Versuche sind auf fünf begrenzt.
- Derselbe Vorschlag erschien doppelt. Jetzt einmal.
- Gescheiterte Schritte waren unsichtbar. Jetzt stehen sie mit ihrem Grund in der Schrittliste
  („Ohne Ergebnis: …“) und im Protokoll des Servers.
- Die Antwort behauptete „keine berechneten Anteile“, obwohl die Ansicht sie zeigt. Die Fakten
  der Selbstprüfung nennen jetzt, dass eine Tabelle Anteile in Prozent zeigt.
- Fünf Namen waren innerhalb der Hochschulsystematik doppelt, ein Zusatz nannte eine Kennung
  („n105“). Gleichnamige Werte desselben Vokabulars heißen jetzt nach ihrer Ebene:
  „Mathematik (Hochschule, Studienbereich)“ und „Mathematik (Hochschule, Studienfach)“.
