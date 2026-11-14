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
