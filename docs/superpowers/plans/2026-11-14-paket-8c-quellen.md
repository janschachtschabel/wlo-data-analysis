# Paket 8c: Datenprobleme und Merkmale mit Bezug zu den Quellen

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:executing-plans. Testgetrieben, danach Review durch einen frischen Reviewer und Befundbehebung.

**Anlass:** Jan (2026-11-14): „für die datenprobleme bei quellen bitte an der quellenpanel app orientieren und weitere typischen datenmerkmale integrieren und in ansichten mit bezug zu den quellen aufbereiten“.

**Goal:** Ein Reiter „Quellen“ unter „Qualität“, der die Sicht des Quellenpanels aus Zählungen nachbildet: Größe und Bindung der Quellen, dünne Metadaten, Qualitätsmerkmale je Bezugsquelle, Vollständigkeit der Quelldatensätze.

## Was das Quellenpanel zeigt und was davon aus Zählungen geht

Das Quellenpanel liest die Quelldatensätze selbst (Materialart „Quelle“, `new_lrt_aggregated/2e678af3-…`) und die Facetten `ccm:oeh_publisher_combined` (Bezugsquelle) und `ccm:replicationsource` (Crawler). Hier bleiben Datensätze unzugänglich, deshalb nur, was sich zählen lässt:

| Quellenpanel | hier |
|---|---|
| BQ_EINZELINHALT, Bezugsquellen mit unter 5 Inhalten | Größenklassen (`sizes`) der Bezugsquellen und der Crawler |
| QD_OHNE_BEZUGSQUELLE, BINDUNG_UNVOLLSTAENDIG | Füllgrad Quelle/Bezugsquelle, „ohne Bezugsquelle“ je Crawler |
| SPIDER_UNEINDEUTIG, DUBLETTE_VERDACHT, Herkunft | Matrix Crawler × Bezugsquelle |
| METADATEN_DUENN | Füllgrad je Bezugsquelle (Fach, Stufe, Materialart, Lizenz, Sprache, Zielgruppe) |
| OER | Anteil offener Lizenzen und „ohne Lizenzangabe“ je Bezugsquelle |
| Kosten, Werbung, Login, DSGVO, Jugendschutz | Merkmal je Bezugsquelle in Prozent (`profile`), dazu Materialart und Sprache |
| OHNE_STATUS, STATUS_INKONSISTENT | Erschließungsstatus je Bezugsquelle und unter den Quelldatensätzen |
| Qualitätsmerkmale der Quelle (18 Felder) | Füllgrad dieser Felder unter den Quelldatensätzen |

Nicht aus Zählungen bestimmbar: Dubletten einzelner Datensätze, Blacklist, Zweitdatensatz, Fehltagging einzelner Quellen, Beschreibung und Schlagwörter (Freitext, keine Facette).

## Neu in der Abfrage

- `profile: {property, top, show: share|count, missing}` mit `rows` (top oder include, höchstens 60): je Zeilenwert eine Abfrage mit der Facette des Merkmals. Geht auch bei Feldern, die das Repositorium nicht als Filter zählt (Kosten, Werbung, Zugang, DSGVO, Altersfreigabe). Zählt das Kriterium der Zeile etwas anderes als ihre Facette, bleibt die Zeile leer (Hinweis `profile_inexact`). „Ohne Angabe“ nur bei einwertigen Feldern.
- `sizes: {property, bounds}`: eine Abfrage, die Werteliste wird in Größenklassen gezählt. Einzelne Werte erscheinen nicht.

## Review Focus

1. Stimmen die Zellen von `profile` gegen die Daten, auch bei gekürzter Facette, leerem Wert, Präfix-Feld?
2. `sizes`: Klassen lückenlos, Summen, gekürzte Werteliste.
3. Das Dashboard gegen WLO Produktion: Welche Qualitätsfelder kennt das Repositorium als Facette, greift der Filter auf die Quelldatensätze?

## Tasks

1. `profile` und `sizes` in Spec und Engine, Tests in `tests/test_sources.py`.
2. Dashboard `backend/wda/builtin/quellen.json` (20 Kacheln), Reiter „Quellen“, Durchstich.
3. Live-Probe auf Jans PC, Felder nachziehen, Review.
