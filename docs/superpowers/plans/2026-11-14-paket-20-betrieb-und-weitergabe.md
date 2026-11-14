# Paket 20: Betrieb und Weitergabe

Ziel: Die App läuft unbeaufsichtigt weiter und gibt Ergebnisse von selbst weiter. Feste Grenzen
bleiben: Nach außen gehen nur Auswertungen (Zählungen), nie Datensätze; der Assistent speichert
nichts von selbst; angehängte Vorlagen werden nicht gespeichert.

Jeder Schritt: erst der Test, dann die Umsetzung, danach `ruff`, `pytest`, `ng test`, Durchstich.

## Schritte

1. **Word-Export.** `report/docx.py` baut eine `.docx` ohne zusätzliche Bibliothek (wie
   `xlsx.py`): Titel, Stand, je Block Überschrift, Text oder Tabelle, Hinweise, Quellenzeilen.
   Diagramme erscheinen als ihre Tabelle. `FORMATS` bekommt `docx`; die Oberfläche bietet
   „Word“ an. Test: Datei ist ein gültiges Zip mit `word/document.xml`, die Zahlen und Titel
   stehen darin, Formelzeichen am Zellanfang sind entschärft wie bei CSV.

2. **Sammelwerte ausblenden.** `rows.exclude` (Werte nach Bezeichnung oder Kennung) nimmt
   Zeilen aus Tabelle und Wissenslandkarte; die Gesamtzeile bleibt die des Bestands, ein Hinweis
   nennt, was ausgeblendet ist. Die Wissenslandkarte blendet „Allgemein“ aus. Test: Zeile fehlt,
   Gesamt unverändert, Hinweis da, unbekannter Wert wird gemeldet.

3. **Wissenslandkarte über Nacht.** `WDA_PRECOMPUTE` (Standard `builtin-themen`) nennt
   Auswertungen, die der Planer einmal je Nacht (zur Uhrzeit von `WDA_SNAPSHOT_NIGHTLY`) neu
   zählt und als Lauf ablegt. Der Reiter zeigt den jüngsten abgelegten Lauf mit seinem Stand
   und dem Knopf „Jetzt neu zählen“. Test: fällig, nur einmal je Nacht, nicht installierte
   Auswertung wird übergangen, Fehler hält den Planer nicht auf.

4. **Versand nach Zeitplan und Schwellen.** Ein Zeitplan kann den Bericht nach dem Lauf
   verschicken: per E-Mail (Anhang PDF, Word, Excel oder HTML; `WDA_SMTP_*`) und an einen
   Webhook (JSON mit Titel, Stand, Auffälligkeiten, verfehlten Zielen; keine Tabellen-Rohdaten
   über die Auswertung hinaus). „Nur bei Auffälligkeit“ verschickt nur, wenn ein Zielwert
   verfehlt ist oder sich eine Zahl auffällig geändert hat. Verfehlte Ziele stehen auch auf der
   Seite „Auffällig“. Ein gescheiterter Versand steht am Zeitplan, der Lauf bleibt gültig.
   Test: mit Attrappen für SMTP und Webhook.

5. **Kommentar des Assistenten zum Bericht.** `POST /api/assistant/comment` mit einem Lauf:
   Der Assistent bekommt die Zahlen des Berichts (gekürzt wie beim Chat) und schreibt einen
   kurzen Kommentar. Er wird angezeigt und lässt sich kopieren; gespeichert wird er nicht.

6. **Gespräche behalten.** Die Unterhaltung bleibt im Browser (localStorage) über das Neuladen
   hinweg erhalten, ohne angehängte Vorlagen; „Neues Gespräch“ löscht sie. Auf dem Server liegt
   weiterhin nichts.

7. **Lesezugang.** Optional `WDA_VIEWER_PASSWORD`: Wer sich damit anmeldet, sieht abgelegte
   Läufe, Berichte, Exporte, „Auffällig“ und die Bibliothek, kann aber nichts ändern, nichts
   ausführen, keine Einstellungen sehen und den Assistenten nicht nutzen. Ohne die Variable
   bleibt es bei dem einen Admin-Zugang. Die Sperre sitzt in `AdminGate` (Erlaubnisliste), nicht
   an den einzelnen Routen. Test: jede Route der App gegen die Erlaubnisliste.

8. **Docker nach den Hostinger-Regeln.** `docker-compose.yml` im Hauptordner mit fertigem Image
   aus GHCR, `init`-Dienst für Admin-Passwort und Sitzungsgeheimnis (Volume, `*_FILE`),
   Traefik-Labels, fest benanntem Netz, nur `${VAR:-default}`; `.env.example` mit leeren
   Feldern; `docker-compose.build.yml` für den lokalen Bau; GitHub Action veröffentlicht das
   Image. Prüfung: `docker compose config -q` ohne `.env` und mit `.env.example` als `.env`
   (soweit Docker in der Umgebung vorhanden ist; sonst YAML-Prüfung und Test der Init-Logik).

## Stand nach Review

Review durch einen unabhängigen Prüfer: kein kritischer, vier wichtige, dreizehn kleine Befunde.
Behoben, jeweils mit Test (`backend/tests/test_review20.py`, `chat.spec.ts`):

- **Versand „nur bei Auffälligkeit“** vergleicht mit dem letzten Lauf desselben Zeitplans
  (`History.changes_since`), nicht mit einem Lauf, der dazwischen von Hand oder über Nacht
  entstand; das gilt auch für Zeitpläne gegen eine andere Instanz. Ein nur teilweise
  gelungener Lauf gilt als auffällig.
- **Lesezugang:** Die Sitzung trägt eine Kennung des Lesezugang-Passworts; ohne Lesezugang
  oder nach einem neuen Passwort gilt sie nicht mehr. Der Lesezugang bekommt keine Links in
  den Explorer und keine leere Überschrift „Zeitpläne“; die Unterhaltung mit dem Assistenten
  wird bei seiner Anmeldung aus dem Browser entfernt.
- **Zielwerte auf „Auffällig“** auch dann, wenn die Kachel sie von ihrer Kennzahl erbt.
- **`rows.exclude`** lehnt leere Einträge ab.
- E-Mails tragen `Date` und `Message-ID`. `PUT /api/schedules/{id}` ohne `send` lässt den
  Versand unverändert, nur `null` löscht ihn. Der Webhook nimmt keine internen Adressen an.
- Der Kommentar des Assistenten erfährt, wenn der Lauf lückenhaft ist, und die Oberfläche
  sagt, wenn er nur einen Teil des Berichts kannte (`truncated`).
- Die Wissenslandkarte zeigt nur einen abgelegten Lauf der jetzigen Fassung.
- Das nächtliche Vorrechnen läuft neben dem Planer; `WDA_PRECOMPUTE=off` schaltet es ab.
- Word: gekürzter Text ist als gekürzt bezeichnet, nummerierte Punkte und Tabellenzeilen im
  Text bleiben eigene Zeilen. `init` nimmt Punycode-Endungen an.
- Wiederhergestellte Unterhaltungen verwerfen Vorschläge, die nicht nach Vorschlag aussehen.

Abweichungen vom Plan, bewusst so belassen:

- Schritt 1: Formelzeichen werden in Word nicht entschärft; Word rechnet Zelltext nicht aus.
- Schritt 2: Ein Wert in `exclude`, den es nicht gibt, bleibt ohne Meldung. Die mitgelieferte
  Wissenslandkarte blendet „Allgemein“ aus und trüge sonst auf jeder Instanz ohne diesen
  Wert einen Dauerhinweis.
- Schritt 7: Die Bibliothek gehört nicht zum Lesezugang (sie führt zum Bearbeiten und
  Ausführen); er sieht Läufe, Berichte, Exporte und „Auffällig“.
- Schritt 8: Das Sitzungsgeheimnis legt die App selbst im Datenverzeichnis an, nicht `init`.

Offen: Wohin ein öffentlicher Webhook-Name auflöst, prüft die App nicht.
