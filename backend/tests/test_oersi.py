import asyncio
import json

import httpx
import pytest

from wda.cache import TTLCache
from wda.edusharing.errors import EduSharingError
from wda.external.oersi import NO_LICENSE, OersiClient, license_key, notation, series_values

URL = "https://oersi.example/resources/api/search/oer_data/_search"


@pytest.mark.parametrize(("url", "key"), [
    ("https://creativecommons.org/licenses/by/4.0/", "CC_BY"),
    ("https://creativecommons.org/licenses/by-sa/3.0/de/", "CC_BY_SA"),
    ("http://creativecommons.org/licenses/by-nc-sa/4.0", "CC_BY_NC_SA"),
    ("https://creativecommons.org/licenses/by-nc-nd/2.0/", "CC_BY_NC_ND"),
    ("https://creativecommons.org/licenses/by-nd/4.0/", "CC_BY_ND"),
    ("https://creativecommons.org/licenses/BY-NC/4.0/", "CC_BY_NC"),
    ("https://creativecommons.org/publicdomain/zero/1.0/", "CC_0"),
    ("https://creativecommons.org/publicdomain/mark/1.0/", "PDM"),
    ("https://www.gnu.org/licenses/gpl-3.0", "CUSTOM"),
    ("https://creativecommons.org/licenses/sampling/1.0/", "CUSTOM"),
    ("https://example.org/creativecommons.org/licenses/by/", "CC_BY"),
    (NO_LICENSE, NO_LICENSE), ("", NO_LICENSE),
])
def test_license_urls_become_repository_keys(url, key):
    assert license_key(url) == key


def test_notation():
    assert notation("http://w3id.org/openeduhub/vocabs/hochschulfaechersystematik/n42") == "n42"
    assert notation("https://w3id.org/kim/hochschulfaechersystematik/n4") == "n4"
    assert notation("http://w3id.org/openeduhub/vocabs/discipline/380") is None
    assert notation("Mathematik") is None


class Oersi:
    def __init__(self):
        self.bodies = []
        self.script = []

    def handler(self, request):
        self.bodies.append(json.loads(request.content))
        if self.script:
            item = self.script.pop(0)
            if isinstance(item, Exception):
                raise item
            return item
        lic = {"sum_other_doc_count": 0, "buckets": [
            {"key": "https://creativecommons.org/licenses/by/4.0/", "doc_count": 600},
            {"key": "https://creativecommons.org/licenses/by/3.0/de/", "doc_count": 50},
            {"key": "https://creativecommons.org/licenses/by-nc-sa/4.0/", "doc_count": 200},
            {"key": "https://www.gnu.org/licenses/gpl-3.0", "doc_count": 30},
            {"key": NO_LICENSE, "doc_count": 20}]}
        aggs = {"license": lic}
        wanted = self.bodies[-1]["aggs"].get("sections")
        if wanted:
            aggs["sections"] = {"buckets": {
                name: {"doc_count": 100 * pos, "license": {"buckets": [
                    {"key": "https://creativecommons.org/licenses/by/4.0/",
                     "doc_count": 60 * pos}]}}
                for pos, name in enumerate(wanted["filters"]["filters"], 1)}}
        if "no_subject" in self.bodies[-1]["aggs"]:
            aggs["no_subject"] = {"doc_count": 70, "license": {"buckets": [
                {"key": "https://creativecommons.org/licenses/by/4.0/", "doc_count": 10}]}}
        return httpx.Response(200, json={"hits": {"total": {"value": 900}},
                                         "aggregations": aggs})


def make(oersi, ttl=900):
    http = httpx.AsyncClient(transport=httpx.MockTransport(oersi.handler))
    return OersiClient(http, TTLCache(ttl=ttl), URL, clock=lambda: 123.0)


def test_counts_only_aggregations_never_hits():
    oersi = Oersi()
    client = make(oersi)
    result = asyncio.run(client.counts({"mint": ["n4", "n42", "n4"], "leer": []}))
    body = oersi.bodies[0]
    assert body["size"] == 0 and "query" not in body and "_source" not in body
    assert body["aggs"]["sections"]["filters"]["filters"] == {"mint": {"terms": {"about.id": [
        "https://w3id.org/kim/hochschulfaechersystematik/n4",
        "https://w3id.org/kim/hochschulfaechersystematik/n42"]}}}
    assert result["total"] == 900 and result["fetched_at"] == 123.0
    assert result["by_license"] == {"CC_BY": 650, "CC_BY_NC_SA": 200, "CUSTOM": 30,
                                    NO_LICENSE: 20}
    assert result["sections"] == {"mint": {"total": 100, "by_license": {"CC_BY": 60}}}
    # „ohne Wert": Einträge ohne Fach, als eigene Zählung
    assert body["aggs"]["no_subject"]["filter"] == {
        "bool": {"must_not": {"exists": {"field": "about.id"}}}}
    assert result["missing"] == {"total": 70, "by_license": {"CC_BY": 10}}
    assert result["cached"] is False


def test_cache_and_refresh():
    oersi = Oersi()
    client = make(oersi)

    async def scenario():
        a = await client.counts({})
        b = await client.counts({})
        c = await client.counts({}, refresh=True)
        return a["cached"], b["cached"], c["cached"]

    assert asyncio.run(scenario()) == (False, True, False)
    assert len(oersi.bodies) == 2 and "sections" not in oersi.bodies[0]["aggs"]


@pytest.mark.parametrize(("answer", "kind"), [
    (httpx.Response(503, text="x"), "unavailable"),
    (httpx.Response(429, text="x"), "throttled"),
    (httpx.Response(400, text="x"), "rejected"),
    (httpx.Response(302, headers={"location": "https://boese.example"}), "rejected"),
    (httpx.Response(200, text="<html>"), "bad_response"),
    (httpx.Response(200, json={"hits": {"total": {"value": "viele"}}}), "bad_response"),
    (httpx.Response(200, json={"hits": {"total": {"value": 5}}, "aggregations": {}}),
     "bad_response"),
    (httpx.Response(200, json={"hits": {"total": {"value": 5}}, "aggregations": {"license": {
        "sum_other_doc_count": 7, "buckets": []}}}), "bad_response"),
    (httpx.Response(200, json={"hits": {"total": {"value": 5}}, "aggregations": {"license": {
        "buckets": [{"key": 5, "doc_count": 1}]}}}), "bad_response"),
    (httpx.ReadTimeout("langsam"), "timeout"),
    (httpx.ConnectError("geheim:passwort@host"), "unavailable"),
])
def test_failures_are_named_and_never_cached(answer, kind):
    oersi = Oersi()
    oersi.script = [answer]
    client = make(oersi)
    with pytest.raises(EduSharingError) as info:
        asyncio.run(client.counts({}))
    assert info.value.kind == kind and "geheim" not in info.value.message
    assert "OERSI" in info.value.message


def test_series_values():
    counts = {"total": 900, "by_license": {"CC_BY": 650, "CC_BY_NC_SA": 200, "CUSTOM": 30,
                                           NO_LICENSE: 20}}
    assert series_values(counts, {"gesamt": "total", "mit": "licensed",
                                  "a": ["CC_0", "PDM", "CC_BY", "CC_BY_SA"],
                                  "b": ["CC_BY_NC_SA", "CC_BY_ND"], "leer": []}) == {
        "gesamt": 900, "mit": 880, "a": 650, "b": 200, "leer": 0}
