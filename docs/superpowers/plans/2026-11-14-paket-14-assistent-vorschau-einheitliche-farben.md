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
