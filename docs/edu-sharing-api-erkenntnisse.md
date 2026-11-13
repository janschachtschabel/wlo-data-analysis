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
