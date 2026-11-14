"""Nachgebautes edu-sharing-Repositorium für Tests (Formate wie live gemessen)."""

from __future__ import annotations

import json

import httpx

EC = "http://w3id.org/openeduhub/vocabs/educationalContext/"
DISC = "http://w3id.org/openeduhub/vocabs/discipline/"

MDS = {
    "id": "oeh",
    "name": "Wir Lernen Online",
    "widgets": [
        {"id": "ccm:taxonid", "caption": "Fach", "type": "multivalueTree", "values": None,
         "condition": {"type": "TOOLPERMISSION"}},
        {"id": "ccm:taxonid", "caption": "Fach", "type": "multivalueTree", "values": [
            {"id": DISC, "caption": "Schulfächer", "parent": None, "description": "x"},
            {"id": DISC + "380", "caption": "Mathematik", "parent": DISC, "icon": "i"},
            {"id": DISC + "460", "caption": "Physik", "parent": DISC},
            {"id": DISC + "20003", "caption": "Alt-Griechisch", "parent": DISC},
            {"id": DISC + "20004", "caption": "Griechisch", "parent": DISC},
            {"id": DISC + "900", "caption": "Sport", "parent": DISC},
            {"id": DISC + "901", "caption": "Sport", "parent": DISC + "900"},
        ]},
        {"id": "ccm:educationalcontext", "caption": "Bildungsstufe",
         "type": "multivalueFixedBadges", "values": [
             {"id": EC + "grundschule", "caption": "Primarstufe", "parent": None},
             {"id": EC + "sekundarstufe_1", "caption": "Sekundarstufe I", "parent": None},
         ]},
        {"id": "ccm:commonlicense_key", "caption": "Lizenz", "type": "text", "values": None},
        {"id": "ccm:commonlicense_key", "caption": None, "type": "text"},
        {"id": "cclom:title", "caption": "Titel", "type": "text"},
        {"caption": "ohne id", "type": "text"},
    ],
    "views": [{"id": "x", "html": "<b>groß</b>"}],
}

SEARCH_PATH = "/edu-sharing/rest/search/v1/queries/-home-/mds_oeh/ngsearch"
MDS_PATH = "/edu-sharing/rest/mds/v1/metadatasets/-home-/mds_oeh"
ABOUT_PATH = "/edu-sharing/rest/_about"

FACET_DATA = {
    "ccm:commonlicense_key": [("CUSTOM", 57256), ("CC_BY_SA", 45449), ("", 87)],
    "ccm:educationalcontext": [(EC + "sekundarstufe_1", 93526), (EC + "grundschule", 45066),
                               ("political", 24)],
    "ccm:taxonid": [(DISC + "380", 26005), (DISC + "460", 9202)],
}


def search_response(body: dict, total: int = 318912) -> dict:
    facets = []
    for spec in reversed(body.get("facets") or []):  # Reihenfolge absichtlich vertauscht
        prop = spec["property"]
        facets.append({
            "property": prop,
            "values": [{"value": v, "count": c} for v, c in FACET_DATA.get(prop, [])],
            "sumOtherDocCount": 6727 if prop == "ccm:oeh_publisher_combined" else 0,
        })
    return {
        "pagination": {"total": total, "from": 0, "count": 0},
        "facets": facets,
        "suggests": None,
        "ignored": None,
        # Ein echtes Repositorium liefert bei maxItems=0 keine Knoten. Der Client muss
        # sie trotzdem verwerfen, falls doch welche kommen.
        "nodes": [{"ref": {"id": "GEHEIMER-KNOTEN"}, "title": "ROHDATEN-DUERFEN-NIE-RAUS"}],
    }


class FakeRepo:
    """Zeichnet Anfragen auf und antwortet wie edu-sharing. ``script`` überschreibt Antworten."""

    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []
        self.script: list[httpx.Response | Exception] = []
        self.mds_error: Exception | httpx.Response | None = None

    @property
    def searches(self) -> list[httpx.Request]:
        return [r for r in self.requests if r.url.path == SEARCH_PATH]

    def bodies(self) -> list[dict]:
        return [json.loads(r.content) for r in self.searches]

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if request.url.path == MDS_PATH:
            if self.mds_error is not None:
                if isinstance(self.mds_error, Exception):
                    raise self.mds_error
                return self.mds_error
            return httpx.Response(200, json=MDS)
        if self.script:
            item = self.script.pop(0)
            if isinstance(item, Exception):
                raise item
            return item
        if request.url.path == ABOUT_PATH:
            return httpx.Response(200, json={
                "version": {"repository": "11.0", "renderservice": "unknown",
                            "major": 1, "minor": 1},
                "services": [{"name": "x"}]})
        if request.url.path == SEARCH_PATH:
            return httpx.Response(200, json=search_response(json.loads(request.content)))
        return httpx.Response(404, json={"error": "NotFound", "message": "nope"})

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.handler)


def error_response(status: int, message: str, error: str = "DAOValidationException"):
    return httpx.Response(status, json={
        "error": error, "message": message, "stacktrace": "at org.edu_sharing.Geheim(…)",
        "logLevel": None, "details": None, "stacktraceArray": ["at org.edu_sharing.Geheim"]})


HIDDEN = "Details hidden: You can configure the output via security.logging.displayLevel"
