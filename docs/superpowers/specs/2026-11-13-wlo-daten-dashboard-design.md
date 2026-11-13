# WLO-Daten-Dashboard: Design

Stand: 2026-11-13 · Status: **freigegeben** (Jan, 2026-11-13), Abschnitt 8 enthält seine Antworten

## 1. Absicht

Ein Quellen- und Daten-Dashboard für WirLernenOnline (WLO) und generisch für
edu-sharing-Repositorien. Es ruft Zahlen live ab, lässt Filter und Ansichten
ähnlich wie in Kibana zusammenstellen (mit KI-Unterstützung) und erzeugt aus
gespeicherten Templates jederzeit reproduzierbare Dashboards und
Zahlen-Dokumente. Es soll Aussagen über Verteilungen und Datenprobleme
ermöglichen. Der Ist-Stand (Tabellen „Qualitätsprobleme von Sammlungen",
„Lizenzarten nach Sammlungen") zeigt nur schmale, schlecht visualisierte
Ausschnitte.

Erstes konkretes Ziel: ein Template, das die Zahlen der
**OER-Statistik (Referatorien)** für WLO ermittelt und darstellt.

## 2. Ziele, Nicht-Ziele, Anti-Ziele

**Ziele**

- Live-Zahlen aus WLO Prod und Staging sowie aus beliebigen
  edu-sharing-Instanzen (Basis-URL + Metadatenset konfigurierbar).
- Filter überall wahlweise per **Label oder URI** eingeben und anzeigen.
- Gespeicherte Filter, Kennzahlen, Templates und Dashboards, in Gruppen
  verwaltet.
- Visualisierungen (Balken, gestapelt, Donut, Heatmap/Kreuztabelle,
  Zeitreihe, Kennzahl-Kachel, Tabelle).
- Export von **Auswertungen** als JSON, CSV, Markdown, HTML, PDF.
- Datenqualität: Füllgrade je Feld, Verteilung der Labels, Problem-Kennzahlen
  je Quelle/Sammlung.
- KI-Assistent, der aus Sprache Filter, Kennzahlen und Dashboards vorschlägt.
- Historie: Kennzahlen über die Zeit, ohne große Datenmengen abzurufen.
- Ein Docker-Container, ein Admin-Zugang.

**Nicht-Ziele**

- Keine Mehrbenutzer- oder Rechteverwaltung.
- Kein Schreiben nach edu-sharing.
- Kein Ersatz für Kibana als freie Abfragesprache; die Abfragen bleiben
  strukturiert (Filter + Aufschlüsselung + Berechnung).

**Anti-Ziele** (das wäre ein Fehlschlag, auch wenn es technisch läuft)

- Rohdaten (einzelne Datensätze aus Cache oder Sync) sind von außen abrufbar.
- Die Oberfläche fühlt sich träge an oder verlangt URIs zu kennen.
- Eine Zahl im Dokument lässt sich nicht auf Abfrage, Instanz und Zeitpunkt
  zurückführen.
- Ein Filter, den das Repositorium nicht kennt, wird still weggelassen und
  die Zahl ist dadurch größer als gefragt.

## 3. Randbedingungen

- edu-sharing liefert über die Suche höchstens 10.000 Treffer je Abfrage.
- Facetten-Werte sind URIs, Labels kommen aus MDS/Vokabular.
- `facets=` verlangt den Property-Namen (`ccm:taxonid`), nicht den Kurznamen.
- LLM über OpenAI-kompatible Schnittstelle mit änderbarer Basis-URL.
  Standard: OpenAI, Modell `gpt-6-luna`. Alternative: B-API
  (`…/api/v1/llm/openai` bzw. Router, Schlüssel als `Authorization: Bearer`).
- Wissen aus `openeduhub/edu-sharing-python-client` und
  `janschachtschabel/quellenpanel` wird genutzt, die Bibliothek selbst nicht
  zwingend.

## 4. Ansätze zur Datenhaltung

Die zentrale Frage ist, woher die Zahlen kommen.

**A. Nur Live-Aggregate.** Jede Kennzahl ist eine `ngsearch`-Abfrage mit
`maxItems=0/1`: `pagination.total` für Zählungen, Facetten für Verteilungen.
Kreuztabellen entstehen aus „Filter × Facette" (z. B. je Bildungsstufe und
Lizenzgruppe eine Facette über Fächer). Kein Datensatz wird gespeichert, das
10.000er-Limit spielt keine Rolle. Schnell und datensparsam, aber
Auswertungen, die der Index nicht als Facette oder Filter kennt (Füllgrad
beliebiger Felder, Dubletten, Textlängen), sind nicht möglich.

**B. Vollständiger Sync in eine lokale Datenbank.** Alles wird abgezogen und
lokal ausgewertet. Maximale Freiheit, aber rund 320.000 Datensätze je
Instanz, lange Erstläufe, Umgehung des 10.000er-Limits als Dauerthema, und
die Rohdaten liegen dann wirklich vor und müssen geschützt werden.

**C. Aggregate zuerst, schmaler Sync nur wo nötig (gewählt).** Standardweg
ist A mit Ergebnis-Cache. Zusätzlich gibt es einen optionalen, inkrementellen
Sync, der nur die für Qualitätsanalysen nötigen Felder in eine interne
Spaltendatenbank schreibt. Analysen, die A nicht kann, laufen darauf.
Das OER-Template und die meisten Dashboards brauchen den Sync nicht.

Entscheidung: **C**. Begründung: A deckt das erste Ziel vollständig ab und
ist sofort schnell; der Sync kommt als eigenes, späteres Paket und bleibt
klein.

## 5. Architektur

Ein Container, zwei Teile:

```
Browser (Angular-SPA)
   │  nur /api/*, Session-Cookie
   ▼
FastAPI-Backend ──────────────► edu-sharing REST (Prod / Staging / eigene Instanz)
   ├─ edu-sharing-Client (httpx, async)      ngsearch, MDS, Vokabulare
   ├─ Abfrage-Engine (QuerySpec → Tabelle)
   ├─ Ergebnis-Cache (TTL, nur Aggregate)
   ├─ Store (SQLite): Instanzen, Filter, Kennzahlen, Templates,
   │                  Dashboards, Gruppen, Läufe/Snapshots
   ├─ Render/Export: JSON, CSV, Markdown, HTML, PDF
   ├─ KI-Assistent (OpenAI-kompatibel, Tool-Aufrufe)
   └─ optional: Sync-Speicher (DuckDB, intern)        ► LLM-Endpunkt
```

### 5.1 Technik (meine Entscheidungen)

- **Backend:** Python 3.12, FastAPI, httpx, Pydantic v2, SQLite (SQLAlchemy).
  Passt zum Wissen aus beiden Referenz-Repos.
- **Frontend:** Angular (aktuelle Version) mit ECharts für Grafiken und einem
  Raster-Layout für Dashboards. Angular, weil quellenpanel und das
  WLO-Umfeld es bereits nutzen.
- **PDF:** der HTML-Bericht wird im Container mit Headless-Chromium gedruckt,
  damit Grafiken im PDF identisch zum Bildschirm sind.
- **KI:** schlanke Tool-Aufruf-Schleife mit dem OpenAI-SDK statt LangGraph.
  Der Assistent erzeugt ausschließlich validierte Spezifikationen
  (QuerySpec, Dashboard-Spec); er bekommt nur Aggregate und Vokabulare zu
  sehen, nie Rohdatensätze. LangGraph lässt sich später ergänzen, falls
  mehrstufige Abläufe nötig werden.

### 5.2 Instanzen

Eine Instanz = Name, Basis-URL, Repository (`-home-`), Metadatenset
(`mds_oeh`), Abfrage (`ngsearch`), optionale Zugangsdaten, Feldprofil
(Kurzname → Property, z. B. `subject → ccm:taxonid`). Vorbelegt:
WLO Prod (`https://redaktion.openeduhub.net`) und WLO Staging
(`https://repository.staging.openeduhub.net`). Weitere Instanzen über die
Oberfläche oder Umgebungsvariablen. Ohne Profil werden Felder und Facetten
aus dem MDS der Instanz gelesen.

### 5.3 Abfrage-Modell

`QuerySpec` ist das einzige Format, in dem Zahlen angefordert werden:

- `instance`, `content_type`
- `filters`: Liste aus Feld + Werten (URI oder Label, wird aufgelöst) +
  optional Volltext
- `breakdown`: 0 bis 2 Felder (Zeilen, Spalten)
- `groups`: benannte Wertegruppen, z. B. Lizenzkategorie A = {CC0, PDM,
  CC BY, CC BY-SA}
- `calc`: Summe, Anteil in Prozent, Rundung, Differenz zum Vorlauf

Ergebnis ist immer eine Tabelle mit Herkunftsangabe (Instanz, Abfragen,
Zeitpunkt, `total_is_lower_bound`, nicht aufgelöste oder vom Repositorium
ignorierte Filter). Nicht auflösbare Filter brechen die Kennzahl mit
Fehlermeldung ab, statt sie still zu verbreitern.

Die Engine zerlegt eine QuerySpec in möglichst wenige Facetten-Abfragen,
führt sie parallel mit Obergrenze aus und cacht jede Teilabfrage (Schlüssel:
Instanz + normalisierte Abfrage, TTL einstellbar, „Neu laden" umgeht den
Cache).

### 5.4 Templates, Dashboards, Verwaltung

- **Filter** und **Kennzahl** (QuerySpec + Name) sind einzeln speicherbar.
- **Dashboard** = Raster aus Kacheln; jede Kachel = Kennzahl + Darstellung.
- **Dokument-Template** = Abschnitte aus Text (Markdown mit Platzhaltern),
  Tabellen und Grafiken; erzeugt einen Bericht zum Abrufzeitpunkt.
- **Gruppen** (Ordner) für alles, dazu Suche, Duplizieren, Import/Export der
  Definitionen als JSON.
- Jeder **Lauf** speichert die Ergebnis-Tabellen (nur Aggregate) mit
  Zeitstempel. Daraus entsteht die Historie.

### 5.5 Historie

Geplante Läufe (z. B. wöchentlich) speichern die Aggregat-Tabellen eines
Templates. Zeitreihen und „Veränderung seit" lesen ausschließlich diese
Snapshots. Das kostet pro Lauf nur die wenigen Facetten-Abfragen.

### 5.6 Sync und 10.000er-Limit (späteres Paket)

Nur für Analysen, die Facetten nicht beantworten. Vorgehen: Die Treffermenge
wird entlang einer Facette (z. B. Quelle/Spider, dann Erstelljahr) so lange
geteilt, bis jede Teilmenge unter 10.000 liegt; jede Teilmenge wird mit
stabiler Sortierung seitenweise gelesen. Folgeläufe lesen nur, was sich seit
dem letzten Lauf geändert hat. Gespeichert werden nur ausgewählte Felder.
Ob Bereichsfilter (dbid, Änderungsdatum) über die Suche funktionieren, wird
zu Beginn dieses Pakets live geprüft; die Aufteilung über Facetten
funktioniert unabhängig davon.

### 5.7 Schutz der Rohdaten

- Alle `/api`-Routen verlangen die Admin-Session (Passwort aus
  Umgebungsvariable, httpOnly-Cookie, ohne gesetztes Passwort startet die App
  nicht).
- Es gibt **keine Route**, die Datensätze aus Cache oder Sync zurückgibt.
  Antworten enthalten nur Aggregat-Tabellen und Definitionen. Ein Test prüft
  das für jede Route.
- Cache und Sync liegen in einem Volume, das der Webserver nicht ausliefert.
- Freigabe-Links ohne Login sind vorerst nicht vorgesehen; Berichte werden
  als Datei exportiert und weitergegeben.
- Beispiel-Datensätze zu einer Zahl (Drilldown) werden nicht gespeichert,
  sondern als Link in die Suche der Instanz geöffnet.

## 6. OER-Statistik-Template

Vorlage: eine Tabelle, Zeilen = Fächer in zehn Fächergruppen mit
Zwischensummen (deutsch/englisch), Spalten = vier Bildungsbereiche
(Elementar, Schule, Beruf/Erwachsene, Hochschule) × Quelle × vier
Lizenzkategorien:

- A: OER (PDM, CC0, CC BY, CC BY-SA)
- B: andere CC-Lizenzen mit NC oder ND
- C: anderer freier Zugang
- D: Lizenzangabe fehlt

Dazu Gesamtzeile, „Total OER (gerundet)" je Bereich und Methodik-Text.

Umsetzung für die WLO-Spalten: je Bereich (Gruppe von
Bildungsstufen-Werten) und Lizenzkategorie eine Facetten-Abfrage über
`ccm:taxonid`, also 16 Abfragen plus Summen. Zuordnungen (Bildungsstufen →
Bereich, Lizenzschlüssel → Kategorie, Fach → Fächergruppe) sind Teil des
Templates und in der Oberfläche änderbar. Ausgabe als Tabelle, gestapelte
Balken je Bereich, Kennzahl-Kacheln und Export in allen Formaten.

**Zählregeln (meine Festlegungen, im Bericht ausgewiesen):**

- Grundgesamtheit: was die öffentliche Suche der Instanz anonym als Material
  liefert (`contentType=FILES`), ohne Sammlungen.
- Ein Material mit mehreren Fächern oder Bildungsstufen zählt in jeder
  zutreffenden Zeile und in jedem zutreffenden Bereich, wie in der Vorlage.
  Gesamtzeile und „Total OER" je Bereich sind dagegen eigene Zählabfragen
  (jedes Material einmal), keine Zeilensummen.
- Kategorie D wird als Differenz berechnet (Gesamtzahl minus Summe aller
  Lizenzwerte), weil die Suche kein Kriterium „Feld fehlt" kennt. Das ist
  exakt, da die Lizenz einwertig ist, und wird in der Herkunftsangabe als
  „abgeleitet" markiert. Die Zeile „(missing value)" für Materialien ohne
  Fach entsteht ebenso.
- Übergeordnete Vokabularwerte verhalten sich so, wie das Repositorium sie
  auswertet; ob Unterwerte mitzählen, wird beim ersten Live-Lauf geprüft und
  im Template festgehalten. Mehrdeutige Labels werden nicht geraten, sondern
  zur Auswahl angeboten.
- Prüfung: Die Zahlen der Vorlage (Stand Oktober 2025) dienen als
  Größenordnungs-Vergleich, nicht als exakter Sollwert.

Die Zeilen der Vorlage stehen in Anhang A.

Die Spalten der anderen Referatorien (Mundo, OERSI, Kita.bayern) stammen
teils aus WLO, teils von außen, siehe Abschnitt 8.

## 7. Pakete

Jedes Paket: Plan → Umsetzung testgetrieben → Review → Befunde beheben.
Übergreifendes Review nach den Paketen 3, 5, 8 und 10.

| # | Paket | Ergebnis |
|---|---|---|
| 1 | Grundgerüst | FastAPI, Konfiguration, Instanzen, Admin-Login, Docker, CI, Platzhalter-Seite |
| 2 | edu-sharing-Client | ngsearch (Zählung, Facetten), MDS, Vokabular Label↔URI, Instanzen, Ergebnis-Cache |
| 3 | Abfrage-Engine | QuerySpec, Gruppen, Kreuztabellen, Berechnungen, Herkunftsangabe |
| 4 | Store und Verwaltung | Filter, Kennzahlen, Templates, Dashboards, Gruppen, Läufe |
| 5 | OER-Statistik und Export | Template, OERSI-Abruf, Bericht, JSON/CSV/Markdown/HTML/PDF |
| 6 | Oberfläche | Filter-Baukasten (Label/URI), Explorer, Dashboards mit Grafiken, Verwaltung |
| 7 | Historie | geplante Läufe, Snapshots, Zeitreihen, „Veränderung seit" |
| 8 | Datenqualität I | was Aggregate hergeben: Label-Verteilungen, Füllgrad einwertiger Felder per Differenz, Kennzahlen je Quelle (Spider, Bezugsquelle) |
| 9 | KI-Assistent | Filter, Kennzahlen und Dashboards aus Sprache |
| 10 | Sync und Datenqualität II | schmaler Sync, Füllgrade beliebiger Felder, Dubletten, Problem-Kennzahlen je Sammlung |

Nach Paket 5 ist das OER-Template über die API nutzbar, nach Paket 6 in der
Oberfläche.

## 8. Entscheidungen von Jan (2026-11-13)

1. **Referatorien im OER-Template.** OERSI-Daten sind in WLO enthalten und
   sollen zusätzlich live über die OERSI-API abrufbar sein. Kita.bayern und
   Mundo liegen eventuell ebenfalls in WLO; das ist unsicher. Folge: Jede
   Referatorien-Spalte hat im Template eine von drei Herkunftsarten:
   *WLO-Filter* (z. B. nach Quelle), *externer Abruf* (zuerst OERSI) oder
   *Handeintrag*. Beim ersten Live-Lauf wird über die Quellen-Facette
   geprüft, welche Referatorien in WLO vorkommen. Der OERSI-Abruf ist Teil
   von Paket 5, weitere externe Quellen nur bei Bedarf.
2. **Kategorie C.** Frei zugängliche Bildungsinhalte im Netz, deren Lizenz
   nicht aus dem Lizenz-Vokabular der offenen Lizenzen stammt. Genauer lässt
   es sich vorab nicht fassen. Folge: C = Lizenzfeld gesetzt, aber weder A
   noch B; D = Lizenzfeld leer. Die Zuordnung der einzelnen Lizenzwerte bleibt
   im Template änderbar und wird am Live-Bestand geprüft.
3. **Grundgesamtheit.** Der anonym sichtbare Bestand reicht für den Anfang.
   Später optional ein Benutzer-Login je Instanz für mehr Sichtbarkeit; das
   Instanz-Modell sieht Zugangsdaten deshalb von Beginn an vor.
4. **Netzzugriff.** Die Cloud-Umgebung bleibt gesperrt. Live-Prüfungen laufen
   über Jans Rechner; die automatischen Tests arbeiten mit aufgezeichneten
   Antworten.

## 9. Dem Bau überlassen

Genaue Tabellen- und Dateistruktur, Wahl der Angular-Raster-Bibliothek,
Cache-TTL-Standardwerte, Aufbau der HTML-Berichtsvorlagen, Farbpalette der
Grafiken.

## Anhang A: Zeilen der OER-Statistik-Vorlage

- **Geisteswissenschaften:** Ethik, Geschichte, Philosophie, Religion
- **Sprache und Kultur:** Interkulturelle Bildung, Alt-Griechisch, Chinesisch,
  Englisch, Esperanto, Französisch, Griechisch, Deutsch, Deutsch als
  Zweitsprache, Italienisch, Latein, Niederdeutsch, Russisch, Sorbisch,
  Spanisch, Türkisch
- **Sport:** Sport
- **Rechts-, Wirtschafts- und Sozialwissenschaften:** Wirtschaftskunde,
  Wirtschaft und Verwaltung, Pädagogik, Mediendidaktik, Medienbildung,
  Politik, Psychologie, Sexualerziehung, Sozialpädagogik, Gesellschaftskunde,
  Sonderpädagogik, Verkehrserziehung, Arbeitssicherheit, Arbeitslehre
- **Mathematik, Naturwissenschaften:** Astronomie, Sachunterricht, Biologie,
  Chemie, Geografie, Mathematik, Physik, MINT
- **Humanmedizin, Gesundheitswissenschaften:** Gesundheit
- **Agrar-, Forst-, Ernährungswissenschaften:** Ernährung und Hauswirtschaft,
  Umweltgefährdung/Umweltschutz, Agrarwirtschaft
- **Ingenieurwissenschaften:** Farbtechnik und Raumgestaltung, Informatik,
  Elektrotechnik, Werken, Hauswirtschaft, Metalltechnik, Bautechnik,
  Textiltechnik und Bekleidung, Holztechnik, Berufliche Bildung
- **Kunst:** Kunst, Darstellendes Spiel, Musik
- **Allgemein/Sonstiges:** Allgemein, Nachhaltigkeit, Open Educational
  Resources, Sucht und Prävention, Ukrainische Schulbücher, Zeitgemäße
  Bildung, Weiterbildung, Sonstiges, (missing value)

Die Zeilenliste stammt aus einer Textextraktion des PDFs und wird beim Bau
gegen das Original und das Fächer-Vokabular der Instanz abgeglichen.
