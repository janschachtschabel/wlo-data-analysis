"""Erzeugt ``wda/builtin/oer-statistik.json`` aus den Vokabularen der Schulfächer und der
Destatis-Systematik (Dateien ``discipline.ttl`` und ``hochschulfaechersystematik.ttl`` aus
https://github.com/openeduhub/oeh-metadata-vocabs).

Aufruf:  python scripts/build_oer_template.py <Ordner mit den .ttl-Dateien>

Die Zuordnungen unten (Fach → Fächergruppe, Bildungsstufe → Bereich, Lizenz → Kategorie)
sind die fachlichen Festlegungen des Templates; nach einer Änderung das Skript neu laufen
lassen und das Ergebnis einchecken.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

DISC = "http://w3id.org/openeduhub/vocabs/discipline/"
HFS = "http://w3id.org/openeduhub/vocabs/hochschulfaechersystematik/"
EC = "http://w3id.org/openeduhub/vocabs/educationalContext/"

# Fächergruppe → (Schulfächer nach Anhang A des Designs, Wurzeln der Destatis-Systematik)
GROUPS: list[tuple[str, str, list[str], list[str]]] = [
    ("geisteswissenschaften", "Geisteswissenschaften",
     ["Ethik", "Geschichte", "Philosophie", "Religion"],
     ["n1", "n01", "n02", "n03", "n04", "n05", "n06", "n18", "n19", "n090"]),
    ("sprache_kultur", "Sprache und Kultur",
     ["Interkulturelle Bildung", "Alt-Griechisch", "Chinesisch", "Englisch", "Esperanto",
      "Französisch", "Deutsch", "Deutsch als Zweitsprache", "Italienisch", "Latein",
      "Niederdeutsch", "Russisch", "Sorbisch", "Spanisch", "Türkisch"],
     ["n07", "n08", "n09", "n10", "n11", "n12", "n13", "n14"]),
    ("sport", "Sport", ["Sport"], ["n2"]),
    ("recht_wirtschaft_soziales", "Rechts-, Wirtschafts- und Sozialwissenschaften",
     ["Wirtschaftskunde", "Wirtschaft und Verwaltung", "Pädagogik", "Mediendidaktik",
      "Medienbildung", "Politik", "Psychologie", "Sexualerziehung", "Sozialpädagogik",
      "Gesellschaftskunde", "Sonderpädagogik", "Verkehrserziehung", "Arbeitssicherheit",
      "Arbeitslehre", "Projektmanagement"],
     ["n3"]),
    ("mathematik_naturwissenschaften", "Mathematik, Naturwissenschaften",
     ["Astronomie", "Sachunterricht", "Biologie", "Chemie", "Geografie", "Mathematik",
      "Physik", "MINT"],
     ["n4"]),
    ("medizin_gesundheit", "Humanmedizin, Gesundheitswissenschaften",
     ["Gesundheit", "Körperpflege", "Evidenzbasierte Medizin"], ["n5"]),
    ("agrar_forst_ernaehrung", "Agrar-, Forst-, Ernährungswissenschaften",
     ["Ernährung und Hauswirtschaft", "Umweltgefährdung, Umweltschutz", "Agrarwirtschaft",
      "Arbeit, Ernährung, Soziales"],
     ["n7"]),
    ("ingenieurwissenschaften", "Ingenieurwissenschaften",
     ["Farbtechnik und Raumgestaltung", "Informatik", "Elektrotechnik", "Werken",
      "Hauswirtschaft", "Metalltechnik", "Bautechnik", "Textiltechnik und Bekleidung",
      "Holztechnik", "Berufliche Bildung", "Mechatronik"],
     ["n8"]),
    ("kunst", "Kunst", ["Kunst", "Darstellendes Spiel", "Musik"], ["n9"]),
    ("allgemein", "Allgemein/Sonstiges",
     ["Allgemein", "Nachhaltigkeit", "Open Educational Resources", "Zeitgemäße Bildung",
      "Weiterbildung", "Sonstiges"],
     ["n0"]),
]
# Wurzeln, deren Teilbaum zu einer anderen Gruppe gehört als der ihres Oberbegriffs
SPLIT_FROM_PARENT = {"n07", "n08", "n09", "n10", "n11", "n12", "n13", "n14"}

SECTORS = [
    ("elementar", "Elementarbereich", ["elementarbereich"]),
    ("schule", "Schule", ["grundschule", "sekundarstufe_1", "sekundarstufe_2"]),
    ("beruf_erwachsene", "Beruf und Erwachsene",
     ["berufliche_bildung", "erwachsenenbildung", "fortbildung"]),
    ("hochschule", "Hochschule", ["hochschule"]),
]
OER_KEYS = ["CC_0", "PDM", "CC_BY", "CC_BY_SA"]
NC_ND_KEYS = ["CC_BY_NC", "CC_BY_NC_SA", "CC_BY_NC_ND", "CC_BY_ND", "CC_BY_SA_NC"]
LICENSE_KEY = "ccm:commonlicense_key"

SERIES = [
    {"id": "gesamt", "label": "Gesamt"},
    {"id": "a", "label": "A: OER", "filters": [
        {"property": "license", "values": ["OPEN", "CC_BY_OPEN"]}]},
    {"id": "b_gruppe", "label": "B ohne CC BY-SA-NC", "filters": [
        {"property": "license", "values": ["CC_BY_RESTRICTED"]}]},
    {"id": "b_sa_nc", "label": "CC BY-SA-NC", "filters": [
        {"property": LICENSE_KEY, "values": [{"value": "CC_BY_SA_NC", "literal": True}]}]},
    {"id": "mit", "label": "mit Lizenzangabe", "filters": [
        {"property": LICENSE_KEY, "values": [{"value": "", "literal": True}]}]},
    {"id": "b", "label": "B: CC mit NC/ND", "formula": "b_gruppe + b_sa_nc"},
    {"id": "c", "label": "C: anderer freier Zugang", "formula": "mit - a - b"},
    {"id": "d", "label": "D: Lizenzangabe fehlt", "formula": "gesamt - mit"},
    {"id": "oer_rund", "label": "OER gerundet", "formula": "round(a, -2)"},
]
SHOW = ["gesamt", "a", "b", "c", "d", "oer_rund"]

INTRO = """\
Stand {{run.date}}, Instanz {{run.instance}}.

Die Tabelle zählt Bildungsmaterialien nach Fach, Bildungsbereich und Lizenzkategorie:

- **A** offene Lizenzen (Public Domain, CC0, CC BY, CC BY-SA)
- **B** andere Creative-Commons-Lizenzen mit NC oder ND
- **C** anderer freier Zugang (Lizenz angegeben, aber weder A noch B)
- **D** Lizenzangabe fehlt
"""

METHOD = """\
## Methodik

- Grundgesamtheit ist, was die Suche der Instanz ohne Anmeldung als Material liefert, ohne
  Sammlungen.
- Ein Material mit mehreren Fächern oder Bildungsstufen zählt in jeder zutreffenden Zeile und
  in jedem zutreffenden Bereich. Die Zeilen der Fächergruppen und die Gesamtzeile sind eigene
  Zählungen, in denen jedes Material einmal vorkommt, keine Summen.
- Die Zeile einer Fächergruppe enthält zusätzlich die Hochschulfächer der Destatis-Systematik,
  die zu ihr gehören.
- Bereiche: Schule = Primarstufe, Sekundarstufe I und II. Beruf und Erwachsene = Berufliche
  Bildung, Erwachsenenbildung, Fortbildung.
- C ist berechnet als Materialien mit Lizenzangabe abzüglich A und B. Dazu gehören auch die
  Angaben „eigene Lizenz“ (CUSTOM) und „keine“ (NONE). D ist die Gesamtzahl abzüglich der
  Materialien mit Lizenzangabe.
- „OER gerundet“ ist A, auf Hunderter gerundet.
- OERSI wird direkt bei oersi.org abgefragt; dort gibt es keine Bildungsstufe, die Spalten
  stehen deshalb für den gesamten Bestand. Zeilen gibt es dort nur für die Fächergruppen
  und für Einträge ohne Fach.
- Mundo und Kita.bayern haben keine Schnittstelle in diesem Werkzeug; ihre Zahlen sind
  Handeinträge in der Definition des Templates.
"""


def concepts(path: Path) -> dict[str, tuple[str, list[str]]]:
    text = path.read_text(encoding="utf-8")
    out = {}
    for match in re.finditer(r"^<([^>]+)> a skos:Concept ;(.*?)\.\s*$", text, re.S | re.M):
        body = match.group(2)
        label = re.search(r'skos:prefLabel[^;]*?"([^"]+)"@de', body)
        out[match.group(1)] = (label.group(1) if label else match.group(1),
                               re.findall(r"skos:broader\s+<([^>]+)>", body))
    return out


def literal(uri: str) -> dict[str, object]:
    # Als fester Wert: Fehlt ein Fach im Metadatenset einer Instanz, zählt es dort 0,
    # statt die ganze Tabelle scheitern zu lassen.
    return {"value": uri, "literal": True}


def build(vocab_dir: Path) -> dict[str, object]:
    disciplines = concepts(vocab_dir / "discipline.ttl")
    by_label = {label: cid for cid, (label, _) in disciplines.items()}
    tree = concepts(vocab_dir / "hochschulfaechersystematik.ttl")
    children: dict[str, list[str]] = {}
    for cid, (_, broader) in tree.items():
        for parent in broader:
            children.setdefault(parent, []).append(cid)

    def subtree(root: str, top: bool = True) -> list[str]:
        if root not in tree:
            raise SystemExit(f"Unbekannter Knoten der Destatis-Systematik: {root}")
        found = [root]
        for child in sorted(children.get(root, [])):
            if child in SPLIT_FROM_PARENT:
                continue
            found += subtree(child, False)
        return found

    labels: dict[str, str] = {}
    sections, used_subjects, used_nodes = [], set(), set()
    for gid, label, subjects, roots in GROUPS:
        missing = [s for s in subjects if s not in by_label]
        if missing:
            raise SystemExit(f"Nicht im Vokabular der Schulfächer: {missing}")
        nodes = sorted({n for root in roots for n in subtree(root)})
        if used_subjects & set(subjects) or used_nodes & set(nodes):
            raise SystemExit(f"Doppelte Zuordnung in {label}")
        labels.update({DISC + by_label[s]: s for s in subjects})
        used_subjects |= set(subjects)
        used_nodes |= set(nodes)
        sections.append({"id": gid, "label": label,
                         "values": [literal(DISC + by_label[s]) for s in subjects],
                         "extra": [literal(HFS + n) for n in nodes]})
    unassigned = sorted(set(by_label) - used_subjects)
    loose = sorted(set(tree) - used_nodes)
    if unassigned or loose:
        raise SystemExit(f"Ohne Fächergruppe: {unassigned} {loose}")

    blocks: list[dict[str, object]] = [
        {"id": sid, "label": f"WLO: {label}", "source": "query", "filters": [
            {"property": "ccm:educationalcontext", "values": [literal(EC + v) for v in levels]}]}
        for sid, label, levels in SECTORS]
    blocks.append({"id": "oersi", "label": "OERSI (direkt)", "source": "oersi", "counts": {
        "gesamt": "total", "mit": "licensed", "a": OER_KEYS, "b_gruppe": NC_ND_KEYS,
        "b_sa_nc": []}})
    for bid, label in (("mundo", "Mundo"), ("kita_bayern", "Kita.bayern")):
        blocks.append({"id": bid, "label": f"{label} (Handeintrag)", "source": "manual",
                       "note": "noch keine Zahlen eingetragen"})

    template = {
        "title": "OER-Statistik", "instance": "wlo-prod",
        "sections": [
            {"id": "einleitung", "type": "text", "markdown": INTRO},
            {"id": "oer", "type": "matrix", "title": "Materialien nach Fach, Bereich und Lizenz",
             "rows": {"property": "ccm:taxonid", "sections": sections, "missing": True,
                      "subtotal": "distinct", "labels": labels},
             "series": SERIES, "show": SHOW, "blocks": blocks},
            {"id": "lizenzen", "type": "chart", "title": "Lizenzangaben im Gesamtbestand",
             "view": {"type": "bar", "options": {}},
             "query": {"instance": "wlo-prod", "rows": {
                 "property": LICENSE_KEY, "missing": True}}},
            {"id": "methodik", "type": "text", "markdown": METHOD},
        ]}
    return {
        "format": "wda-definitions", "version": 1,
        "builtin": {"id": "oer-statistik", "title": "OER-Statistik",
                    "description": "Materialien nach Fach, Bildungsbereich, Quelle und "
                                   "Lizenzkategorie, nach dem Vorbild der OER-Statistik der "
                                   "Referatorien."},
        "groups": [{"id": "builtin-berichte", "name": "Mitgelieferte Berichte"}],
        "definitions": [{
            "id": "builtin-oer-statistik", "kind": "template", "name": "OER-Statistik",
            "description": "Fächer in Fächergruppen × Bildungsbereiche × Quelle × "
                           "Lizenzkategorien. Zuordnungen sind in der Definition änderbar.",
            "group_id": "builtin-berichte", "spec": template}],
    }


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit(__doc__)
    target = Path(__file__).resolve().parent.parent / "wda" / "builtin" / "oer-statistik.json"
    target.write_text(json.dumps(build(Path(sys.argv[1])), ensure_ascii=False, indent=1) + "\n",
                      encoding="utf-8")
    print(f"geschrieben: {target}")
