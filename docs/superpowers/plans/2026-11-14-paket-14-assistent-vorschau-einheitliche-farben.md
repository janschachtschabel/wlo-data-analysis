# Paket 14: Assistent zeigt Dashboards als Vorschau, einheitliche Farben

**Anlass:** Jan (2026-11-14, 12:30 Berlin), mit vier Bildschirmfotos: „die geforderte grafik macht er nicht . falls das nicht geht und es gibt einen grund dafür z.b. zuviele daten - braucht der user zumindest eine rückmeldung. auch das anlegen des dashboards scheint nicht so zu laufen wie gedacht - man hätte am ende einen absprunglink auf das fertige dashboard bekommen sollen. felder ohne schraffur darzustellen - sondern nur in rot ist gut - aber diese felder brauchen keinen rahmen - wenn andere felder auch keine rahmen haben. zwischen versschiedenen seiten unterscheidet sich die visualisierung von farben und darstellung ohne ersichtlichen grund. die roten strichen seitlich an den zellen sind nicht notwendig.“

**Goal:** Ein vorgeschlagenes Dashboard ist im Chat mit seinen Grafiken zu sehen. Der Assistent behauptet nicht, etwas angelegt zu haben, und sagt, wenn eine Grafik nicht passt. Nach dem Anlegen führt ein deutlicher Link zum Dashboard. Rot bedeutet auf allen Seiten dasselbe und sieht gleich aus.

## Ursachen

- `propose_dashboard` rechnete die Vorschau, schickte den Vorschlag aber mit `report: None`; die Karte zeigte nur die Titel der Kacheln. „Zeig das als Grafik“ ergab wieder so eine Liste.
- Das Modell schrieb „ist erstellt“, obwohl erst der Knopf „Dashboard anlegen“ speichert. Nach dem Klick wurde aus dem Knopf ein unauffälliger Link „Gespeichert – öffnen“.
- Lücken (9 % Rot mit Rahmen) und „unter 50 %“ (30 % Rot mit Strich links) waren zwei verschiedene Rottöne mit zwei verschiedenen Zusätzen.

## Entscheidungen

- Der Vorschlag eines Dashboards trägt den Bericht der Vorschau; die Karte zeigt ihn als Raster mit den Breiten der Kacheln.
- Der Assistent legt weiterhin nichts selbst an (Speichern löst der Admin aus). Der Leitfaden verbietet „erstellt“/„angelegt“ und verlangt bei Grafikwunsch eine grafische Darstellung je Kachel oder eine Begründung. Das Werkzeug meldet dem Modell die Darstellungen und den Hinweis auf den Knopf zurück.
- Nach dem Speichern steht an der Karte „Das Dashboard ist angelegt.“ mit „Dashboard öffnen“ und „bearbeiten“; vorher „Vorschau, noch nicht angelegt“.
- Ein Rot für Lücke und „unter 50 %“ (22 % in der Oberfläche, `#f2c4c0` im Export), ohne Rahmen und ohne Strich. Blau bleibt für Mengen, die Ampel für Anteile; die Legende unter jeder Tabelle sagt, welche Skala gilt.

## Review Focus

1. Größe und Inhalt des Vorschlags mit Bericht: gelangt mehr als Aggregate nach außen oder ans Modell?
2. Kann der Assistent jetzt doch etwas speichern? Stimmt der Link nach dem Anlegen (Dashboard und Kennzahl)?
3. Lesbarkeit der Zahlen auf dem neuen Rot, hell und dunkel, im Export.

## Tasks

1. `llm/tools.py` (Bericht am Vorschlag, Rückmeldung), `llm/assistant.py` (Leitfaden), Test in `tests/test_llm.py`.
2. `assistant/assistant.ts` (Vorschau als Raster, Meldung und Link nach dem Anlegen), Durchstich.
3. `shared/result-table.ts`, `report/heat.py` (ein Rot, keine Rahmen und Striche).
4. Review, Befunde, Probe mit echtem Modell auf Jans Rechner.

## Probe mit echtem Modell (Jans Rechner, Stand c63e39a)

- „Baue mir ein Dashboard zum Bestand der Grundschule.“: 10,9 s, Vorschau mit fünf Blöcken (bar, bar, bar, pie, bar). Der Text sagt „als Vorschau vorgeschlagen“ und nennt den Knopf. Nichts gespeichert.
- „Zeig das als Grafik.“ danach: wieder fünf Grafiken, aber ein fast gleiches zweites Dashboard; Kreis mit 11 bis 15 Segmenten.
- „Wie offen ist das Material je Fach? Zeig es als Grafik.“: gestapelte Balken, aber nur nach Schule und Hochschule gruppiert statt je Fach.
- Folge: Leitfaden nachgeschärft („je Fach“ heißt eine Zeile je Wert; bar bis 15, pie bis 8 Werte; vorhandene Grafiken nicht noch einmal bauen).

## Stand nach Review

Frischer Reviewer: kein kritischer, kein wichtiger Befund. Der Bericht am Vorschlag enthält nur Zählungen, ans Modell geht davon nichts; der Assistent speichert nichts; die Links nach dem Anlegen führen auf funktionierende Seiten. Kontrast der Zahl auf dem neuen Rot: 10,9:1 hell, 9,1:1 dunkel, 11,1:1 im Export.

Behoben:

- Die Vorschau eines Dashboards war nach oben nicht begrenzt: über 400.000 Zeichen zeigt die Karte nur die Titel, und das Modell erfährt es (Test in `tests/test_llm.py`).
- Für Folgefragen kannte das Modell von einem Dashboard nur die Titel: Der Verlauf nennt jetzt je Kachel Darstellung und Abfrage (Grenze 8.000 Zeichen).
- Die Breiten der Kacheln werden einmal je Vorschlag berechnet statt bei jeder Prüfung der Ansicht.

Offen, gering: Lücken heben sich im Graustufendruck nur schwach ab (gewollt, ohne Rahmen).
