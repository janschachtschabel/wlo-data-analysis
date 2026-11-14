import asyncio
import base64
import json

import httpx
import pytest

from wda.cache import TTLCache
from wda.edusharing.client import Criterion, EduSharingClient
from wda.edusharing.errors import EduSharingError
from wda.instances import Instance

from .esfake import DISC, EC, HIDDEN, SEARCH_PATH, FakeRepo, error_response

INSTANCE = Instance(id="t", name="T", base_url="https://repo.example")


def make(repo: FakeRepo, instance: Instance = INSTANCE, **kwargs):
    http = httpx.AsyncClient(transport=repo.transport())
    slept: list[float] = []

    async def sleep(seconds):
        slept.append(seconds)

    client = EduSharingClient(instance, http, TTLCache(ttl=900), sleep=sleep, **kwargs)
    client.slept = slept
    return client


def run(coro):
    return asyncio.run(coro)


def test_request_shape():
    repo = FakeRepo()
    client = make(repo)
    agg = run(client.aggregate(
        [Criterion("ccm:educationalcontext", (EC + "sekundarstufe_1",)),
         Criterion("license", ("OPEN", "CC_BY_OPEN"))],
        facets=["ccm:taxonid", "ccm:commonlicense_key"]))
    request = repo.searches[0]
    assert request.method == "POST"
    assert str(request.url).startswith("https://repo.example" + SEARCH_PATH + "?")
    assert dict(request.url.params) == {"contentType": "FILES", "maxItems": "0", "skipCount": "0"}
    assert request.headers["accept"] == "application/json"
    assert "authorization" not in request.headers
    body = json.loads(request.content)
    assert body == {
        "criteria": [
            {"property": "ccm:educationalcontext", "values": [EC + "sekundarstufe_1"]},
            {"property": "license", "values": ["CC_BY_OPEN", "OPEN"]},
        ],
        "facets": [{"property": "ccm:commonlicense_key"}, {"property": "ccm:taxonid"}],
        "facetLimit": 2000,
        "facetMinCount": 1,
    }
    assert agg.total == 318912
    assert agg.cached is False
    assert [(v.value, v.count) for v in agg.facets["ccm:taxonid"].values] == [
        (DISC + "380", 26005), (DISC + "460", 9202)]
    assert agg.facets["ccm:commonlicense_key"].values[2].value == ""
    assert agg.facets["ccm:taxonid"].truncated is False


def test_no_facets_no_criteria():
    repo = FakeRepo()
    agg = run(make(repo).aggregate([]))
    assert repo.bodies()[0] == {"criteria": [], "facets": [], "facetLimit": 2000,
                                "facetMinCount": 1}
    assert agg.total == 318912 and agg.facets == {}


def test_truncated_facet():
    repo = FakeRepo()
    agg = run(make(repo).aggregate([], facets=["ccm:oeh_publisher_combined"], facet_limit=30))
    facet = agg.facets["ccm:oeh_publisher_combined"]
    assert facet.other_count == 6727 and facet.truncated is True
    assert repo.bodies()[0]["facetLimit"] == 30


def test_nodes_are_dropped():
    repo = FakeRepo()
    agg = run(make(repo).aggregate([], facets=["ccm:taxonid"]))
    assert "GEHEIM" not in repr(agg) and "ROHDATEN" not in repr(agg)
    assert not hasattr(agg, "nodes")


@pytest.mark.parametrize("criteria", [
    [Criterion("ccm:taxonid", ("a",)), Criterion("ccm:taxonid", ("b",))],
    [Criterion("ccm:taxonid", ())],
    [Criterion("", ("a",))],
    [Criterion(" ccm:taxonid", ("a",))],
    [Criterion("ccm:taxonid", ("a" * 2001,))],
    [Criterion("ccm:taxonid", tuple(str(i) for i in range(5001)))],
])
def test_invalid_query_never_sent(criteria):
    repo = FakeRepo()
    with pytest.raises(EduSharingError) as info:
        run(make(repo).aggregate(criteria))
    assert info.value.kind == "invalid_query"
    assert repo.requests == []


@pytest.mark.parametrize("kwargs", [
    {"facet_limit": 0}, {"facet_limit": 5001}, {"content_type": "ALLES"},
    {"facets": [""]}, {"facets": ["ccm:taxonid "]}, {"facets": ["a b"]},
])
def test_invalid_parameters_never_sent(kwargs):
    repo = FakeRepo()
    with pytest.raises(EduSharingError) as info:
        run(make(repo).aggregate([], **kwargs))
    assert info.value.kind == "invalid_query"
    assert repo.requests == []


def test_empty_string_value_is_a_value():
    """``ccm:commonlicense_key = ""`` zählt alle Dokumente mit gesetztem Schlüssel; in
    Wertelisten kommt der leere Wert als Facettenwert vor und muss filterbar sein."""
    repo = FakeRepo()
    client = make(repo)
    run(client.aggregate([Criterion("ccm:commonlicense_key", ("",))]))
    run(client.aggregate([Criterion("ccm:educationalcontext", ("political", ""))]))
    assert repo.bodies()[0]["criteria"] == [{"property": "ccm:commonlicense_key", "values": [""]}]
    assert repo.bodies()[1]["criteria"][0]["values"] == ["", "political"]


@pytest.mark.parametrize(("message", "kind"), [
    ("Could not find parameter ccm:gibtesnicht in the query ngsearch", "unknown_property"),
    ("Widget ccm:gibtesnicht was not found in the mds oeh", "unknown_facet"),
    ("Trying to search for multiple values of a non-multivalue field x", "single_value"),
    (HIDDEN, "rejected"),
])
def test_400_classification(message, kind):
    repo = FakeRepo()
    repo.script = [error_response(400, message)]
    with pytest.raises(EduSharingError) as info:
        run(make(repo).aggregate([Criterion("ccm:gibtesnicht", ("x",))]))
    err = info.value
    assert (err.kind, err.status) == (kind, 400)
    assert "Geheim" not in err.message and "stacktrace" not in err.message.lower()
    assert len(repo.requests) == 1  # 400 wird nicht wiederholt


@pytest.mark.parametrize(("response", "kind"), [
    (httpx.Response(200, text="<html>Wartung</html>"), "bad_response"),
    (httpx.Response(200, content=b""), "bad_response"),
    (httpx.Response(200, json={"facets": []}), "bad_response"),
    (httpx.Response(200, json={"pagination": {"total": "viele"}}), "bad_response"),
    (httpx.Response(200, json=[1, 2]), "bad_response"),
    (httpx.Response(200, json={"pagination": {"total": 5},
                               "facets": [{"property": "x", "values": [{"value": "a"}]}]}),
     "bad_response"),
    (httpx.Response(200, json={"pagination": {"total": 5}, "facets": 5}), "bad_response"),
    (httpx.Response(200, json={"pagination": {"total": 5},
                               "facets": [{"property": "x", "values": 5}]}), "bad_response"),
    (httpx.Response(200, json={"pagination": {"total": -5}}), "bad_response"),
    (httpx.Response(200, json={"pagination": {"total": 5},
                               "facets": [{"property": "x", "values": [
                                   {"value": "a", "count": -1}]}]}), "bad_response"),
    (httpx.Response(200, json={"pagination": {"total": 5},
                               "facets": [{"property": "x", "values": [],
                                           "sumOtherDocCount": "viele"}]}), "bad_response"),
    (httpx.Response(302, json={"pagination": {"total": 5}},
                    headers={"location": "https://boese.example/"}), "bad_response"),
    (httpx.Response(401, json={"error": "x"}), "rejected"),
    (httpx.Response(403, text="nein"), "rejected"),
    (error_response(500, "all shards failed", "DAOException"), "unavailable"),
])
def test_bad_responses(response, kind):
    repo = FakeRepo()
    repo.script = [response] * 3
    with pytest.raises(EduSharingError) as info:
        run(make(repo).aggregate([]))
    assert info.value.kind == kind


def test_retry_on_503_then_success():
    repo = FakeRepo()
    repo.script = [httpx.Response(503, text="x"), httpx.Response(502, text="x")]
    client = make(repo)
    agg = run(client.aggregate([]))
    assert agg.total == 318912
    assert len(repo.requests) == 3
    assert client.slept == [0.5, 1.0]


def test_retries_exhausted():
    repo = FakeRepo()
    repo.script = [httpx.Response(504, text="x")] * 3
    with pytest.raises(EduSharingError) as info:
        run(make(repo).aggregate([]))
    assert info.value.kind == "unavailable" and info.value.status == 504
    assert len(repo.requests) == 3


def test_timeout_and_connect_error():
    repo = FakeRepo()
    repo.script = [httpx.ReadTimeout("zu langsam")] * 3
    with pytest.raises(EduSharingError) as info:
        run(make(repo).aggregate([]))
    assert info.value.kind == "timeout"
    assert len(repo.requests) == 1  # Timeouts werden nicht wiederholt
    repo2 = FakeRepo()
    repo2.script = [httpx.ConnectError("kein Netz")] * 3
    with pytest.raises(EduSharingError) as info2:
        run(make(repo2).aggregate([]))
    assert info2.value.kind == "unavailable" and info2.value.status is None


def test_cache_key_ignores_order_and_refresh_reloads():
    repo = FakeRepo()
    client = make(repo)

    async def scenario():
        a = await client.aggregate(
            [Criterion("license", ("OPEN", "CC_BY_OPEN")), Criterion("ccm:taxonid", ("x",))],
            facets=["ccm:taxonid", "ccm:educationalcontext"])
        b = await client.aggregate(
            [Criterion("ccm:taxonid", ("x",)), Criterion("license", ("CC_BY_OPEN", "OPEN"))],
            facets=["ccm:educationalcontext", "ccm:taxonid", "ccm:taxonid"])
        assert (a.cached, b.cached) == (False, True)
        assert a.fetched_at == b.fetched_at
        assert len(repo.searches) == 1
        c = await client.aggregate([Criterion("ccm:taxonid", ("y",))])
        assert c.cached is False and len(repo.searches) == 2
        d = await client.aggregate([Criterion("ccm:taxonid", ("y",))], refresh=True)
        assert d.cached is False and len(repo.searches) == 3
        await client.aggregate([Criterion("ccm:taxonid", ("y",))], facet_limit=5)
        await client.aggregate([Criterion("ccm:taxonid", ("y",))], content_type="ALL")
        assert len(repo.searches) == 5

    run(scenario())


def test_concurrent_identical_requests_hit_repository_once():
    repo = FakeRepo()
    client = make(repo)

    async def scenario():
        results = await asyncio.gather(*[client.aggregate([], facets=["ccm:taxonid"])
                                         for _ in range(10)])
        assert {r.total for r in results} == {318912}

    run(scenario())
    assert len(repo.searches) == 1


def test_concurrency_limit():
    active = peak = 0

    async def handler(request):
        nonlocal active, peak
        active += 1
        peak = max(peak, active)
        await asyncio.sleep(0.01)
        active -= 1
        return httpx.Response(200, json={"pagination": {"total": 1}, "facets": []})

    async def scenario():
        http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        client = EduSharingClient(INSTANCE, http, TTLCache(ttl=900), max_concurrency=2)
        await asyncio.gather(*[client.aggregate([Criterion("ccm:taxonid", (str(i),))])
                               for i in range(8)])

    run(scenario())
    assert peak == 2


def test_basic_auth_only_with_credentials():
    repo = FakeRepo()
    inst = Instance(id="a", name="A", base_url="https://repo.example",
                    username="nutzer", password="pass wörd")
    run(make(repo, inst).aggregate([]))
    expected = base64.b64encode("nutzer:pass wörd".encode()).decode()
    assert repo.requests[0].headers["authorization"] == f"Basic {expected}"


def test_custom_repository_metadataset_query_are_url_encoded():
    repo = FakeRepo()
    inst = Instance(id="a", name="A", base_url="https://repo.example/unter",
                    repository="mein repo", metadataset="mds/x", query="q?1")
    repo.script = [httpx.Response(200, json={"pagination": {"total": 7}, "facets": []})]
    agg = run(make(repo, inst).aggregate([]))
    assert agg.total == 7
    assert repo.requests[0].url.raw_path.decode().split("?")[0] == (
        "/unter/edu-sharing/rest/search/v1/queries/mein%20repo/mds%2Fx/q%3F1")


def test_aggregate_shape_is_closed():
    """Das Ergebnis hat genau diese Felder; für Datensätze gibt es keinen Platz."""
    import dataclasses

    from wda.edusharing.client import Aggregate, Facet, FacetValue

    assert [f.name for f in dataclasses.fields(Aggregate)] == [
        "total", "facets", "fetched_at", "cached"]
    assert [f.name for f in dataclasses.fields(Facet)] == ["property", "values", "other_count"]
    assert [f.name for f in dataclasses.fields(FacetValue)] == ["value", "count"]


def test_about_only_passes_known_keys():
    repo = FakeRepo()
    repo.script = [httpx.Response(200, json={"version": {
        "repository": "11.0", "major": 1, "intern": "geheim", "flag": True, "x": {"a": 1}}})]
    assert run(make(repo).about()) == {"repository": "11.0", "major": 1}


def test_about_and_metadataset():
    repo = FakeRepo()
    client = make(repo)
    assert run(client.about()) == {"repository": "11.0", "renderservice": "unknown",
                                   "major": 1, "minor": 1}
    mds = run(client.metadataset())
    assert mds["id"] == "oeh"


def test_error_message_never_contains_credentials():
    repo = FakeRepo()
    inst = Instance(id="a", name="A", base_url="https://repo.example",
                    username="nutzer", password="streng-geheim")
    repo.script = [httpx.ConnectError("verbindung zu nutzer:streng-geheim@repo fehlgeschlagen")] * 3
    with pytest.raises(EduSharingError) as info:
        run(make(repo, inst).aggregate([]))
    assert "streng-geheim" not in str(info.value) and "streng-geheim" not in info.value.message


def test_ignored_criteria_are_an_error_not_a_bigger_number():
    """Befund aus dem übergreifenden Review: ``ignored`` wurde verworfen."""
    repo = FakeRepo()
    repo.script = [httpx.Response(200, json={
        "pagination": {"total": 318912}, "facets": [],
        "ignored": ["ccm:foo", "<script>böse</script>"]})]
    with pytest.raises(EduSharingError) as info:
        run(make(repo).aggregate([Criterion("ccm:foo", ("x",))]))
    assert info.value.kind == "ignored" and "ccm:foo" in info.value.message
    assert "script" not in info.value.message
    for harmless in (None, []):
        repo = FakeRepo()
        repo.script = [httpx.Response(200, json={
            "pagination": {"total": 5}, "facets": [], "ignored": harmless})]
        assert run(make(repo).aggregate([])).total == 5


def test_same_instance_id_with_other_address_does_not_share_cache():
    repo = FakeRepo()
    http = httpx.AsyncClient(transport=repo.transport())
    cache = TTLCache(ttl=900)
    one = EduSharingClient(INSTANCE, http, cache)
    two = EduSharingClient(
        Instance(id="t", name="T", base_url="https://anderes-repo.example"), http, cache)

    async def scenario():
        a = await one.aggregate([])
        b = await two.aggregate([])
        c = await one.aggregate([])
        return a.cached, b.cached, c.cached

    assert run(scenario()) == (False, False, True)
    assert len(repo.searches) == 2


def test_throttling_is_retried_and_named():
    repo = FakeRepo()
    repo.script = [httpx.Response(429, text="x")]
    client = make(repo)
    assert run(client.aggregate([])).total == 318912 and client.slept == [0.5]
    repo = FakeRepo()
    repo.script = [httpx.Response(429, text="x")] * 3
    with pytest.raises(EduSharingError) as info:
        run(make(repo).aggregate([]))
    assert info.value.kind == "throttled" and "drosselt" in info.value.message
    repo = FakeRepo()
    repo.script = [httpx.Response(404, text="x")]
    with pytest.raises(EduSharingError) as info:
        run(make(repo).aggregate([]))
    assert "404" in info.value.message and "Basis-URL" in info.value.message
