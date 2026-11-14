# Paket 8b: Abdeckung und Datenprobleme

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:executing-plans. Jede Aufgabe testgetrieben, danach Review durch einen frischen Reviewer und Befundbehebung. Das übergreifende Review der Pakete 6 bis 8 schließt dieses Paket ein.

**Anlass:** Rückmeldung von Jan (2026-11-14): Die Oberfläche ist „noch sehr simpel“. Gewünscht sind eine Matrix Fach × Bildungsstufe, die Angebot und Lücken zeigt (mit Hochschulfächern), eine Ansicht für Datenprobleme, keine falschen Felder in den Auswahllisten, mehr sinnvolle Ansichten. Die KI-Funktionen folgen direkt danach als Paket 9.

**Goal:** Aus Zählungen sichtbar machen, wo das Angebot dicht und wo es dünn ist, und wo die Metadaten nicht stimmen. Weiter ohne einen einzelnen Datensatz.

## Was gemessen ist

- `ccm:taxonid` trägt zwei Vokabulare in einem Feld: Schulfächer (`…/vocabs/discipline/…`) und Hochschulfächer nach Destatis (`…/vocabs/hochschulfaechersystematik/…`). Python-Client (`search.py`: `"subject": "ccm:taxonid"`) und Quellenpanel (`ccm:taxonid_DISPLAYNAME`) benutzen dasselbe Feld.
- `virtual:*`-Felder (z. B. `virtual:editorial_taxonid`) sind Suchhilfen der Redaktionsoberfläche, tragen dieselben Bezeichnungen wie die echten Felder und zählen nichts. Sie standen bisher in den Auswahllisten.
- Felder mischen Schreibweisen (Werbung: URI, Zahl, Sterne); in Facetten stehen Werte, die das Vokabular nicht kennt.
- Einwertig laut Metadatenset (`singleoption`, `radioVertical` …): die Facettensumme ist die Zahl der Materialien mit Wert.

## Engine

1. `rows.match` und `columns.match`: nur Werte, die mit diesem Text beginnen (Trennung der Vokabulare an der URI). Wirkt vor `top`.
2. `rows.vocab: "outside" | "inside"`: nur Werte, die das Vokabular des Feldes nicht kennt bzw. kennt. Nur für Felder mit Werteliste.
3. `rows.missing` bei einwertigen Feldern, die das Repositorium nicht als Filter zählt: Gesamtzahl minus Facettensumme; der Hinweis `rows_overlap` entfällt bei einwertigen Feldern.
4. `QuerySpec.vocab_check: {fields}`: je Feld eine Zeile mit „verschiedene Werte“, „davon unbekannt“, „Zuordnungen“, „davon mit unbekanntem Wert“, „Anteil %“. Schließt `rows`, `columns`, `series`, `fill` aus. Ein Aufruf je Feld.
5. Mitgelieferte Vorlagen gelten als unverändert, wenn sie nach dem Einlesen dasselbe ergeben (ein neues optionales Feld der Abfrage macht eine installierte Vorlage nicht „geändert“).

## Darstellung

- Neue Darstellung `heatmap`: die Tabelle mit eingefärbten Zellen. Farbskala je Tabelle über die Zählungen (Wurzel-Skala, damit kleine Werte sichtbar bleiben); 0 ist als Lücke markiert, unbekannt bleibt leer. Für Prozent-Spalten (Füllgrad) eine Ampel: unter 50 rot, unter 80 gelb, sonst grün. In der Oberfläche und im HTML-/PDF-Export; CSV, JSON und Markdown bleiben Zahlen.
- Explorer: Darstellung „Heatmap“ bei Zeilen × Spalten und bei Füllgraden je Zeilenfeld; Schalter „nur Schulfächer / nur Hochschulfächer“ entsteht aus `match`, wenn das Feld mehrere Vokabulare trägt (aus den Werten der Werteliste abgeleitet).
- Feldlisten überall ohne `virtual:*`.

## Mitgeliefert

- **Abdeckung** (`abdeckung`): Schulfächer × Bildungsstufe, Hochschulfächer × Bildungsstufe, Schulfächer × Materialart, Bildungsstufe × Materialart, Fächer × Lizenzkategorie (offen, CC mit NC/ND, andere, ohne Lizenz), Quelle × Bildungsstufe. Alle als Heatmap.
- **Datenprobleme** (`datenprobleme`): fehlende Kernangaben (Ampel), Lücken je Quelle (Ampel), Werte außerhalb der Vokabulare (Übersicht und je Feld die betroffenen Werte), gemischte Schreibweisen (Werbung, Qualitätsfelder), Lizenzangaben, die nichts sagen (CUSTOM, NONE, leer), Materialien ohne Quelle.
- Navigation: Eintrag „Qualität“ öffnet die drei mitgelieferten Dashboards (Abdeckung, Füllgrade, Datenprobleme) als Reiter; fehlt eines, wird es angelegt.

## Global Constraints

- Keine neue Route für Daten; `tests/test_routes_guard.py` bleibt unverändert. Der Reiter „Qualität“ benutzt Installieren und Ausführen der bestehenden Schnittstelle.
- Werte außerhalb des Vokabulars sind aggregierte Feldwerte mit Zählung, nie Datensätze. Nur Felder mit Werteliste; freie Textfelder (Schlagwörter, Autor) sind ausgeschlossen, weil ihre Werte einzelne Datensätze kennzeichnen können.
- Unbekannt ist `null`, nie 0.

## Review Focus

1. Richtigkeit gegen den Testbestand: `match`, `vocab`, `missing` aus der Summe, `vocab_check`.
2. `rows.vocab` und `vocab_check` dürfen für Felder ohne Werteliste nichts liefern (Sicherheit).
3. Heatmap: Skala, Lücken, Dunkelmodus, Kontrast der Zahlen, Export.
4. Bestehende Definitionen und Läufe bleiben gültig.

## Tasks

1. Feldlisten ohne `virtual:*`; Vorlagenvergleich (Punkt 5).
2. Engine: `match`, `vocab`, `missing` aus der Summe, `vocab_check`; Tests gegen rohe Zählung.
3. Heatmap in Oberfläche und Export.
4. Bundles `abdeckung` und `datenprobleme`, Reiter „Qualität“.
5. Review, Befundbehebung, Live-Probe.

## Stand nach Live-Probe (2026-11-14, WLO Produktion, Commit 2f65d14)

Alle 39 Kacheln der drei Dashboards laufen (2 bis 4 s je Dashboard); zwei Zellen der Heatmap direkt gegen das Repositorium geprüft, beide exakt. Behoben aus der Probe:

- Füllgrad „Sprache“ war leer: Das Kriterium trifft auch andere Schreibweisen („de“ findet „DE“ und „de-DE“), die Probe verlangte Gleichheit. Sie lässt jetzt den Überschuss zu, den Werte erklären, die den häufigsten enthalten.
- Zeilen einer Matrix aus `columns` wurden nach der ersten Spalte sortiert und gekürzt; jetzt nach der Zeilensumme.
- Hochschulfächer: neue Zeilenoption `rollup: "top"` (Fächergruppen samt aller Fächer darunter). Die Wurzel der Systematik ist keine Zeile mehr.
- Schmutzwerte verlängerten die Heatmaps; die Kacheln der Abdeckung zeigen nur Werte der Werteliste (`vocab: "inside"`), die übrigen stehen unter Datenprobleme.
- Zwei leere Kacheln (Materialart, Zielgruppe außerhalb des Vokabulars) entfernt; Titel der Übersicht nennt abweichende Schreibweisen.
- Explorer: Schalter „Vokabular“ (aus den URIs der Werteliste) und „oberste Ebene“.
