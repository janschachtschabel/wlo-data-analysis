"""Kleiner Testbestand, der die live gemessene Zähl-Semantik von edu-sharing nachbildet.

Regeln (siehe docs/edu-sharing-api-erkenntnisse.md): ODER innerhalb eines Kriteriums, UND
zwischen Kriterien, Präfix-Treffer beim Lizenzschlüssel, Lizenzgruppen über ``license``,
Facetten ignorieren das Kriterium auf dem eigenen Feld, ``facetLimit`` × 5 Werte.
"""

from __future__ import annotations

import json
import random
from collections import Counter
from collections.abc import Callable

import httpx

from .esfake import MDS_PATH, SEARCH_PATH, error_response

EC = "http://w3id.org/openeduhub/vocabs/educationalContext/"
DISC = "http://w3id.org/openeduhub/vocabs/discipline/"
SUBJECTS = {"Mathematik": DISC + "380", "Physik": DISC + "460", "Deutsch": DISC + "120",
            "Geschichte": DISC + "240", "Informatik": DISC + "320"}
LEVELS = {"Elementarbereich": EC + "elementarbereich", "Primarstufe": EC + "grundschule",
          "Sekundarstufe I": EC + "sekundarstufe_1", "Hochschule": EC + "hochschule"}
LICENSES = ["CC_0", "PDM", "CC_BY", "CC_BY_SA", "CC_BY_NC", "CC_BY_NC_SA", "CC_BY_ND",
            "COPYRIGHT_FREE", "CUSTOM"]
LICENSE_GROUPS = {
    "OPEN": {"CC_0", "PDM"},
    "CC_BY_OPEN": {"CC_BY", "CC_BY_SA"},
    "CC_BY_RESTRICTED": {"CC_BY_NC", "CC_BY_NC_SA", "CC_BY_ND"},
    "COPYRIGHT_OTHERS": {"COPYRIGHT_FREE"},
}
UNUSED_LEVELS = {"Sekundarstufe II": EC + "sekundarstufe_2",
                 "Berufliche Bildung": EC + "berufliche_bildung",
                 "Förderschule": EC + "foerderschule"}
SOURCES = ["oersi_spider", "youtube_spider", "bpb_spider"]
KNOWN = {"ccm:taxonid", "ccm:educationalcontext", "ccm:commonlicense_key", "license",
         "ccm:replicationsource", "ngsearchword", "ccm:editorial_checklist",
         "ccm:oeh_lrt_aggregated", "cclom:general_language", "ccm:oeh_publisher_combined",
         "ccm:educationalintendedenduserrole", "ccm:curriculum", "ccm:oeh_widgets",
         "cclom:general_keyword"}
# Wie live gemessen: als Facette zählbar, als Kriterium abgelehnt
FACET_ONLY = {"ccm:oeh_languageLevel",
              "ccm:price", "ccm:oeh_buffet_criteria", "virtual:kita_search",
              "ccm:conditionsOfAccess", "ccm:containsAdvertisement",
              "ccm:dataProtectionConformity", "ccm:fskRating", "ccm:oeh_quality_login"}
# weitere Felder mit Werteliste: (Bezeichnung, Widget-Art, Werte, Anteil gefüllt)
MORE = {
    "ccm:oeh_lrt_aggregated": ("Materialart", "multivalueTree",
                               ["Video", "Arbeitsblatt", "Bild", "Kurs", "Audio"], 0.75),
    "cclom:general_language": ("Sprache", "multivalueBadges", ["de", "en", "fr"], 0.75),
    "ccm:educationalintendedenduserrole": ("Zielgruppe", "multivalueFixedBadges",
                                           ["teacher", "learner", "parent"], 0.5),
    "ccm:curriculum": ("Lehrplanthema", "multivalueTree", ["thema-1", "thema-2"], 0.08),
    "ccm:oeh_widgets": ("Widget-Typ", "multioption", ["erklaervideo", "worksheet"], 0.06),
    "ccm:conditionsOfAccess": ("Zugang", "singleoption",
                               ["no_login", "login", "login_for_additional_features"], 0.4),
    "ccm:containsAdvertisement": ("Werbung", "singleoption", ["no", "yes"], 0.27),
    "ccm:dataProtectionConformity": ("Datenschutz", "singleoption",
                                     ["DSGVO-konform", "nicht konform"], 0.03),
    "ccm:fskRating": ("Altersfreigabe", "singleoption", ["0", "6", "12"], 0.01),
    "ccm:oeh_quality_login": ("Qualität: Login", "singleoption", ["0", "1"], 0.1),
}
PUBLISHERS = ["bpb", "Youtube", "OERSI", "Serlo"]
PRICES = ["no", "yes", "yes_for_additional"]
BUFFET = ["speech_valid", "content_valid", "medial_relevant"]

HSF = "http://w3id.org/openeduhub/vocabs/hochschulfaechersystematik/"
# Hochschulfächer (Destatis) stehen bei WLO im selben Feld wie die Schulfächer
UNI_SUBJECTS = {"Geisteswissenschaften": HSF + "n1", "Ingenieurwissenschaften": HSF + "n8",
                "Mathematik, Naturwissenschaften": HSF + "n4"}

MDS = {"id": "oeh", "widgets": [
    {"id": "ccm:taxonid", "caption": "Fach", "type": "multivalueTree",
     "values": [{"id": uri, "caption": label}
                for label, uri in {**SUBJECTS, **UNI_SUBJECTS}.items()]},
    {"id": "ccm:educationalcontext", "caption": "Bildungsstufe",
     "type": "multivalueFixedBadges",
     "values": [{"id": uri, "caption": label} for label, uri in LEVELS.items()] + [
         # in der Werteliste, aber an keinem Material des Testbestands
         {"id": uri, "caption": label} for label, uri in UNUSED_LEVELS.items()]},
    {"id": "ccm:commonlicense_key", "caption": "Lizenz", "type": "text"},
    {"id": "ccm:replicationsource", "caption": "Quelle", "type": "text"},
    {"id": "cclom:title", "caption": "Titel", "type": "text"},
    {"id": "ccm:price", "caption": "Kosten", "type": "singleoption",
     "values": [{"id": v, "caption": v} for v in PRICES]},
    {"id": "ccm:oeh_buffet_criteria", "caption": "Buffet-Kriterien", "type": "multioption",
     "values": [{"id": v, "caption": v} for v in BUFFET]},
    {"id": "ccm:editorial_checklist", "caption": "Erschließungsstatus", "type": "multioption",
     "values": [{"id": v, "caption": v} for v in ("2", "9")]},
    {"id": "ccm:oeh_languageLevel", "caption": "Sprachniveau", "type": "multioption",
     "values": [{"id": v, "caption": v} for v in ("A1", "A2", "B1", "B2")]},
    {"id": "virtual:kita_search", "caption": "Kita", "type": "singleoption",
     "values": [{"id": "1", "caption": "ja"}]},
    {"id": "ccm:oeh_publisher_combined", "caption": "Bezugsquelle", "type": "text"},
    *[{"id": prop, "caption": caption, "type": kind,
       "values": [{"id": v, "caption": v} for v in values]}
      for prop, (caption, kind, values, _) in MORE.items()],
]}

Doc = dict[str, list[str]]
TIME_CRITERIA = {"virtual:time_after_created": "cm:created",
                 "virtual:time_after_modified": "cm:modified"}


def _iso(ms: int) -> str:
    from datetime import UTC, datetime
    return datetime.fromtimestamp(ms / 1000, UTC).isoformat().replace("+00:00", "Z")


def make_docs(n: int = 400, seed: int = 7) -> list[Doc]:
    rng = random.Random(seed)  # noqa: S311 - Testdaten
    docs: list[Doc] = []
    for _ in range(n):
        doc: Doc = {}
        k = rng.choice([0, 1, 1, 1, 2, 3])
        doc["ccm:taxonid"] = rng.sample(list(SUBJECTS.values()), k)
        k = rng.choice([0, 1, 1, 2])
        doc["ccm:educationalcontext"] = rng.sample(list(LEVELS.values()), k)
        if rng.random() < 0.05:
            doc["ccm:educationalcontext"].append("political")  # Schmutzwert ohne URI
        doc["ccm:commonlicense_key"] = [rng.choice(LICENSES)] if rng.random() < 0.7 else []
        doc["ccm:replicationsource"] = [rng.choice(SOURCES)]
        docs.append(doc)
    extra = random.Random(seed + 1000)  # noqa: S311 - eigene Folge, damit das Übrige bleibt
    for doc in docs:
        doc["ccm:price"] = [extra.choice(PRICES)] if extra.random() < 0.5 else []
        doc["ccm:oeh_buffet_criteria"] = extra.sample(BUFFET, extra.choice([0, 0, 2, 3]))
        doc["ccm:editorial_checklist"] = [extra.choice(["2", "9"])] if extra.random() < 0.1 else []
    level = random.Random(seed + 2000)  # noqa: S311
    for doc in docs:
        doc["ccm:oeh_languageLevel"] = (
            [level.choice(["A1", "A2", "B1", "B2"])] if level.random() < 0.15 else [])
    for doc in docs:
        if extra.random() < 0.2:
            doc["ccm:taxonid"] = [*doc["ccm:taxonid"], extra.choice(list(UNI_SUBJECTS.values()))]
        doc["ccm:oeh_publisher_combined"] = (
            [extra.choice(PUBLISHERS)] if extra.random() < 0.95 else [])
        for prop, (_, kind, values, share) in MORE.items():
            many = 2 if kind.startswith("multi") and extra.random() < 0.3 else 1
            doc[prop] = extra.sample(values, many) if extra.random() < share else []
    return docs


def _matches(doc: Doc, prop: str, values: list[str]) -> bool:
    if prop == "ngsearchword":
        return True
    if prop == "license":
        keys = set().union(*(LICENSE_GROUPS.get(v, set()) for v in values))
        return bool(keys & set(doc.get("ccm:commonlicense_key", [])))
    if prop == "ccm:editorial_checklist":  # live gemessen: vergleicht nicht exakt
        return bool(doc.get("ccm:educationalcontext"))
    if prop == "cclom:general_language":  # live gemessen: „de“ trifft „DE“, „de_DE“, „Deutsch“
        return any(have.casefold().startswith(v.casefold())  # ohne Groß/Klein, Wortanfang
                   for v in values for have in doc.get(prop, []))
    if prop == "ccm:commonlicense_key":  # einwertig, Präfix-Treffer
        return any(key.startswith(values[0]) for key in doc.get(prop, []))
    return bool(set(values) & set(doc.get(prop, [])))


class DataRepo:
    def __init__(self, docs: list[Doc] | None = None) -> None:
        self.docs = docs if docs is not None else make_docs()
        self.requests: list[httpx.Request] = []
        self.node_requests = 0

    @property
    def searches(self) -> list[dict]:
        return [json.loads(r.content) for r in self.requests if r.url.path == SEARCH_PATH]

    def brute(self, pred: Callable[[Doc], bool]) -> int:
        return sum(1 for d in self.docs if pred(d))

    def has(self, prop: str, *values: str) -> Callable[[Doc], bool]:
        return lambda d: bool(set(values) & set(d.get(prop, [])))

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if request.url.path == MDS_PATH:
            return httpx.Response(200, json=MDS)
        if request.url.path != SEARCH_PATH:
            return httpx.Response(200, json={"version": {"repository": "11.0"}})
        body = json.loads(request.content)
        criteria = {c["property"]: c["values"] for c in body["criteria"]}  # letztes gewinnt
        since = {p: int(criteria.pop(p)[0]) for p in list(criteria) if p in TIME_CRITERIA}
        for prop, values in criteria.items():
            if prop not in KNOWN:
                return error_response(
                    400, f"Could not find parameter {prop} in the query ngsearch")
            if prop == "ccm:commonlicense_key" and len(values) > 1:
                return error_response(
                    400, "Trying to search for multiple values of a non-multivalue field x")

        def select(skip: str | None = None) -> list[Doc]:
            return [d for n, d in enumerate(self.docs)
                    if all(_matches(d, p, v) for p, v in criteria.items() if p != skip)
                    and all(self.time_of(n, d, TIME_CRITERIA[p]) >= t
                            for p, t in since.items())]

        size = int(request.url.params.get("maxItems", "0"))
        if size:  # Seiten mit Knoten: nur der lokale Abzug fragt so
            self.node_requests += 1
            start = int(request.url.params.get("skipCount", "0"))
            if start + size > 10000:
                return error_response(500, "all shards failed")
            key = request.url.params.get("sortProperties", "cm:created")
            wanted = set(request.url.params.get_list("propertyFilter"))
            chosen = {id(d) for d in select()}
            ranked = sorted(((self.time_of(n, d, key), n, d)
                             for n, d in enumerate(self.docs) if id(d) in chosen),
                            key=lambda item: item[:2])
            nodes = [self.node(n, d, wanted) for _, n, d in ranked[start:start + size]]
            return httpx.Response(200, json={
                "pagination": {"total": len(ranked), "from": start, "count": len(nodes)},
                "facets": [], "nodes": nodes})

        facets = []
        for spec in body.get("facets") or []:
            prop = spec["property"]
            if prop not in KNOWN | FACET_ONLY and prop != "cclom:title":
                return error_response(400, f"Widget {prop} was not found in the mds oeh")
            counts = Counter(v for d in select(skip=prop) for v in d.get(prop, []))
            ranked = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
            shown = ranked[: body["facetLimit"] * 5]
            facets.append({
                "property": prop,
                "values": [{"value": v, "count": c} for v, c in shown],
                "sumOtherDocCount": sum(c for _, c in ranked[len(shown):]),
            })
        return httpx.Response(200, json={
            "pagination": {"total": len(select()), "from": 0, "count": 0},
            "facets": facets, "nodes": []})

    # ---- Knoten für den lokalen Abzug ----------------------------------------------
    T0 = 1_600_000_000_000  # Erstellzeit des ersten Materials in Millisekunden

    def time_of(self, n: int, doc: Doc, key: str) -> int:
        fixed = doc.get("_" + key)
        if fixed:
            return int(fixed[0])
        step = 3_600_000 if key == "cm:created" else 7_200_000
        return self.T0 + n * step

    def node(self, n: int, doc: Doc, wanted: set[str]) -> dict:
        props = {k: v for k, v in doc.items()
                 if not k.startswith("_") and v and (not wanted or k in wanted)}
        if n % 5:  # Freitext, den nur der Abzug sieht
            props.setdefault("cclom:title", [f"Material Nummer {n % 37}"])
        if n % 3:
            props.setdefault("cclom:general_description", ["Beschreibung " * (n % 9 + 1)])
        if n % 4:
            props.setdefault("ccm:wwwurl", [f"https://seite{n % 11}.example/pfad/{n % 150}"])
        props.setdefault("cclom:general_keyword", [f"wort{i}" for i in range(n % 4)])
        created, modified = self.time_of(n, doc, "cm:created"), self.time_of(n, doc,
                                                                             "cm:modified")
        props["cm:created"], props["cm:modified"] = [str(created)], [str(modified)]
        return {"ref": {"id": doc.get("_id", [f"node-{n}"])[0], "repo": "local"},
                "title": props.get("cclom:title", [""])[0], "name": f"n{n}",
                "createdAt": _iso(created), "modifiedAt": _iso(modified),
                "properties": props, "preview": {"url": "https://r.example/preview"}}

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.handler)
