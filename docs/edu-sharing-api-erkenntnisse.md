# edu-sharing-Suche: gemessenes Verhalten

Gemessen am 2026-11-13, anonym, gegen WLO Produktion (`redaktion.openeduhub.net`,
edu-sharing 11.0) und Staging. Alle Zahlen sind Momentaufnahmen und dienen nur der
Einordnung. Diese Datei ist die Grundlage für Client und Abfrage-Engine.

## Abfrage

`POST {basis}/edu-sharing/rest/search/v1/queries/-home-/mds_oeh/ngsearch?contentType=FILES&maxItems=0&skipCount=0`

```json
{"criteria":[{"property":"ccm:educationalcontext","values":["…/sekundarstufe_1"]}],
 "facets":[{"property":"ccm:taxonid"}],
 "facetLimit":2000,"facetMinCount":1}
```

Antwort:

```json
{"pagination":{"total":318912,"from":0,"count":0},
 "facets":[{"property":"ccm:taxonid","values":[{"value":"…","count":26005}],"sumOtherDocCount":0}],
 "suggests":null,"ignored":null,"nodes":[]}
```

- `maxItems=0` funktioniert; `pagination.total` ist nicht bei 10.000 gedeckelt
  (Prod 318.912, Staging 403.779).
- **Facetten sind Objekte** (`{"property": …}`), Zeichenketten ergeben 400.
- Mehrere Facetten je Abfrage sind möglich; die Reihenfolge der Antwort weicht ab,
  also über `property` zuordnen.
- `criteria: []` entspricht `ngsearchword=*`.
- Der Server liefert bis zu **5 × `facetLimit`** Werte; der Rest steht in
  `sumOtherDocCount`. Mit `facetLimit=2000` kam alles.
- `facetSuggest` im Body filtert Facettenwerte per Teilzeichenkette.

## Zählregeln

- **Facetten zählen nur Dokumente mit Wert.** Lizenz-Facette Prod: Summe 230.715 von
  318.912, also 88.197 ohne Lizenzschlüssel.
- **Mehrere Werte in einem Kriterium = ODER.** Bei einwertigen Feldern ist das die Summe,
  bei mehrwertigen Feldern nicht (sek I ∪ sek II = 104.912, einzeln 93.526 und 81.784).
- **Verschiedene Properties = UND.**
- **Dieselbe Property in zwei Kriterien: das letzte gewinnt** (kein UND, kein Fehler).
  Der Client lehnt das deshalb ab.
- **Facetten sind nach den Kriterien anderer Properties gefiltert**, ignorieren aber
  das Kriterium auf der eigenen Property.
- **Kein Aufrollen in Hierarchien.** Ein Oberfach zählt nur, was direkt damit
  verschlagwortet ist. Fach samt Unterfächern = alle IDs in ein Kriterium.
- **„Ohne Wert" bei mehrwertigen Feldern:** Gesamtzahl minus Zählung mit allen
  vorkommenden Werten der Property als ein Kriterium (ODER).

## Lizenzen

- `ccm:commonlicense_key` als **Kriterium** nimmt nur einen Wert und matcht als
  **Präfix**: `CC_BY` zählt alle `CC_BY*` (150.986 statt 43.637). Der leere Wert `""`
  zählt alle Dokumente mit gesetztem Schlüssel (230.715). Exakte Zahlen je Schlüssel
  kommen aus der Facette oder durch Abziehen der längeren Schlüssel.
- Kriterium **`license`** (mehrwertig, exakt) kennt Gruppen:
  `OPEN` = CC_0 + PDM · `CC_BY_OPEN` = CC_BY + CC_BY_SA ·
  `CC_BY_RESTRICTED` = CC_BY_NC_SA, CC_BY_NC_ND, CC_BY_NC, CC_BY_ND ·
  `COPYRIGHT_OTHERS` = COPYRIGHT_FREE, COPYRIGHT_LICENSE, SCHULFUNK.
  Zu keiner Gruppe gehören CUSTOM, CC_BY_SA_NC, NONE und leere Werte.
- Vorkommende Schlüssel (Prod): CUSTOM 57.256, CC_BY_SA 45.449, CC_BY_NC_SA 44.090,
  CC_BY 43.637, COPYRIGHT_FREE 17.923, CC_BY_NC_ND 15.472, CC_0 2.584,
  COPYRIGHT_LICENSE 1.475, CC_BY_NC 1.129, CC_BY_ND 712, CC_BY_SA_NC 497, PDM 344,
  leer 87, NONE 41, UNTERRICHTS_UND_LEHRMEDIEN 15, SCHULFUNK 4.
- `virtual:editorial_license` (einwertig: `all`, `oer`, `none_oer`, `licensed`, `none`)
  weicht leicht ab (`oer` 92.077 gegenüber 92.014) und wird nicht verwendet.

## Kriterien der Query `ngsearch`

Das Metadatenset liefert die Query-Definition nicht; die Liste ist durch Probieren
entstanden und nicht garantiert vollständig.

- Mehrwertig: `license`, `ccm:taxonid`, `ccm:educationalcontext`, `ccm:oeh_lrt_aggregated`,
  `ccm:oeh_lrt`, `ccm:replicationsource`, `ccm:oeh_publisher_combined`,
  `ccm:educationalintendedenduserrole`, `ccm:oeh_extendedType`,
  `ccm:educationallearningresourcetype`, `ccm:curriculum`, `ccm:oeh_widgets`,
  `ccm:editorial_checklist`, `cclom:general_keyword`, `cclom:general_language`,
  `ccm:author_freetext`, `virtual:collection_id_tree`, `virtual:collection_id_primary`.
- Einwertig: `ngsearchword`, `ccm:commonlicense_key`, `ccm:wwwurl`, `virtual:kita_search`,
  `virtual:editorial_license`, `virtual:time_after_created`, `virtual:time_after_modified`.
- Nicht vorhanden: `cm:created`, `cm:modified`, `sys:node-dbid`, `cm:name`, `cclom:title`.
- **Zeit:** nur „seit" (`virtual:time_after_created`, `virtual:time_after_modified`),
  ein Wert als Epoch-Millisekunden oder ISO-Datum. Zeitfenster = Differenz zweier
  „seit"-Zählungen. Kein „bis", kein dbid-Bereich.

## Fehler

Fehlerantworten: `error, message, stacktrace, logLevel, details, stacktraceArray`.
Produktion verbirgt den Text, Staging zeigt ihn.

| Fall | Status |
|---|---|
| unbekannte Kriterium-Property | 400 (`Could not find parameter …`) |
| unbekannte Facetten-Property | 400 (`Widget … was not found`) |
| mehrere Werte bei einwertiger Property | 400 (`multiple values of a non-multivalue field`) |
| unbekannter Wert bei bekannter Property | 200, total 0 |
| `skipCount + maxItems > 10000` | 500 (`all shards failed`) |

`ignored` ist `null` oder `[]` und wurde in keinem Test befüllt.

## Metadatenset

`GET {basis}/edu-sharing/rest/mds/v1/metadatasets/-home-/mds_oeh`: 17 MB, davon etwa
14 MB für drei Kompetenz-Widgets mit je 14.756 Werten. 235 Widgets, 209 verschiedene IDs
(Varianten mit `template`/`condition`). Werte haben `id, caption, parent, …`.
`ccm:taxonid` 416 Werte (Baum), `ccm:educationalcontext` 12, `ccm:oeh_lrt_aggregated` 48,
`ccm:commonlicense_key` ohne Werteliste. In den Facetten kommen Werte vor, die das
Metadatenset nicht kennt (Schmutzwerte ohne URI, leere Werte).

## Quellen

- OERSI in WLO: `ccm:replicationsource=oersi_spider` (Prod 49.671) bzw.
  `ccm:oeh_publisher_combined=OERSI` (49.427).
- Mundo/Sodix und kita.bayern kommen in Produktion nicht als Quelle vor.
  `oai_sodis_spider`/„Sodis" ist eine andere Quelle.
- `ccm:oeh_publisher_combined` hat in Produktion 3.604 Werte.

## OERSI direkt

`POST https://oersi.org/resources/api/search/oer_data/_search` mit
`{"size":0,"track_total_hits":true,"aggs":{"lic":{"terms":{"field":"license.id","size":50}}}}`
liefert 200, `hits.total.value` 104.893. `license.id` enthält volle URLs mit Versions-
und Ländervarianten, die zum Gruppieren normalisiert werden müssen.

## Füllgrade und Kennzeichen (gemessen 2026-11-13, Produktion, 318.912 Materialien)

- Füllgrad über das Kriterium „alle vorkommenden Werte" stimmt für die Kernfelder mit den
  Facettensummen überein: Fach 74,9 %, Sprache 74,8 %, Materialart 75,9 %, Lizenz 72,3 %,
  Bildungsstufe 54,7 %, Zielgruppe 51,0 %, Quelle 93,7 %, Bezugsquelle 98,3 %.
- **Nur als Facette, nicht als Kriterium (400):** alle `ccm:oeh_quality_*`,
  `ccm:oeh_accessibility_*`, `ccm:oeh_competence_*`, `ccm:price`, `ccm:conditionsOfAccess`,
  `ccm:containsAdvertisement`, `ccm:license_oer`, `ccm:fskRating`,
  `ccm:dataProtectionConformity`, `ccm:accessibilitySummary`, `ccm:oeh_buffet_criteria`,
  `ccm:oeh_languageLevel`, `ccm:oeh_languageTarget`, `cclom:status`. Verteilungen gehen,
  Filter nicht. Der Füllgrad ergibt sich bei einwertigen Feldern aus der Facettensumme
  (`ccm:price` 158.675 = 49,8 %); bei mehrwertigen (`ccm:oeh_buffet_criteria`: Summe
  129.585, häufigster Wert 25.928) bleibt nur eine Spanne.
- **Kriterium angenommen, aber nicht exakt:** `ccm:editorial_checklist` (Wert „9": 2.765
  Treffer statt 25.153 laut Facette; alle Werte: 129.396 statt höchstens 28.607),
  `ccm:author_freetext` (Textsuche), `cclom:general_keyword` (zwei Werte ergeben 0). Die
  Engine prüft deshalb jede Zählung gegen die Facette (häufigster Wert ≤ Zählung ≤ Summe).
- `virtual:*`-Felder und `license` sind Suchhilfen, keine gespeicherten Felder: Facette
  ohne Werte.
- Qualitätsfelder mischen Schreibweisen (Zahl 0 bis 5 und URI derselben Stufe),
  `ccm:containsAdvertisement` mischt `no`/`yes` mit Zahlen; einige Felder tragen nur `TODO`.
- Praktisch leer: `ccm:editorial_state` (13), `ccm:oeh_signatures` (8), `cclom:status` (72).
- Füllgrad je Quelle (25 größte): 0,34 s, 10 Abfragen. Auffällig: bpb ohne Bildungsstufe
  (1,2 %), GeoGebra ohne Fach (2,0 %), Tutory ohne Sprache (2,9 %).

## Seitenweises Lesen aller Materialien (gemessen 2026-11-14, Produktion)

Für den lokalen Abzug (`backend/wda/snapshot/`). `skipCount + maxItems` über 10.000
beantwortet die Instanz mit 500; alles darüber geht nur mit einem wandernden Zeitkriterium.

- `sortProperties=cm:created&sortAscending=true` sortiert verlässlich, ebenso
  `cm:modified` und `sys:node-dbid`.
- `propertyFilter` lässt sich wiederholen und begrenzt die gelieferten Eigenschaften;
  der Rahmen des Knotens bleibt (7 bis 20 kB je Material).
- Seitengrößen 100, 500, 1000 und 2000 gehen; 1000 war am schnellsten (rund 15 s je
  Seite, 67 Materialien je Sekunde).
- Fortsetzen: Kriterium `virtual:time_after_created` mit **Millisekunden seit 1970 als
  Text**. Es gilt einschließlich (≥) und auf die Millisekunde genau. ISO-Schreibweisen
  werden auf den Tag gekürzt und taugen dafür nicht. Dasselbe gilt für
  `virtual:time_after_modified` mit `cm:modified`.
- Im Knoten: `ref.id`, `createdAt` und `modifiedAt` als ISO-Text, in `properties`
  `cm:created` und `cm:modified` als 13-stellige Texte; Werte sind Listen von Texten,
  fehlende Felder fehlen ganz.
- Probe mit der Anwendung (12.988 Materialien in 362 s): rund 36 Materialien je
  Sekunde, eine Seite zu 1000 im Mittel 27 s. Hochrechnung für WLO: rund 320 Seiten,
  etwa zweieinhalb Stunden, 2 bis 6 GB Übertragung, Datei rund 180 MB.
