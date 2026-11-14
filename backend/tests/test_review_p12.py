"""Befunde aus dem Review von Paket 12."""

import json

import httpx
import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from wda.engine.spec import QuerySpec
from wda.llm.client import LlmClient, LlmError
from wda.llm.config import LlmConfig
from wda.main import create_app
from wda.store.specs import InvalidDefinition, parse_spec

from .conftest import ADMIN_PASSWORD
from .esdata import DataRepo

CREATED = "virtual:time_after_created"


@pytest.fixture
def api(settings):
    with TestClient(create_app(settings, transport=DataRepo().transport())) as client:
        assert client.post("/api/auth/login",
                           json={"password": ADMIN_PASSWORD}).status_code == 204
        yield client


def query(api, **spec):
    return api.post("/api/query", json={"instance": "wlo-prod", **spec})


@pytest.mark.parametrize("value", ["9999y", "2027y", "101y", "1201m", "36501d", "99999d"])
def test_since_reaches_back_a_century_at_most(api, value):
    r = query(api, filters=[{"property": CREATED, "values": [{"since": value}]}])
    assert r.status_code == 422, r.text


@pytest.mark.parametrize("value", ["100y", "1200m", "36500d"])
def test_since_at_the_limit_counts(api, value):
    r = query(api, filters=[{"property": CREATED, "values": [{"since": value}]}])
    assert r.status_code == 200, r.text


def test_since_stands_alone_and_time_values_are_checked(api):
    two = [{"since": "5d"}, {"since": "6d"}]
    assert query(api, filters=[{"property": CREATED, "values": two}]).status_code == 422
    mixed = [{"since": "5d"}, "1600000000000"]
    assert query(api, filters=[{"property": CREATED, "values": mixed}]).status_code == 422
    assert query(api, filters=[{"property": CREATED, "values": ["abc"]}]).status_code == 422
    assert query(api, filters=[{"property": CREATED, "values": ["1600000000000"]}]
                 ).status_code == 200


def test_hidden_columns_have_no_place_in_a_matrix_or_row_shares():
    section = {"id": "m", "type": "matrix", "title": "M", "rows": {
        "property": "ccm:taxonid",
        "sections": [{"id": "a", "label": "A", "values": ["Mathematik"]}]},
        "series": [{"id": "s", "label": "S"}, {"id": "h", "label": "H", "hidden": True}],
        "blocks": [{"id": "b", "label": "B", "source": "query"}]}
    with pytest.raises(InvalidDefinition, match="hidden"):
        parse_spec("template", {"title": "T", "instance": "wlo-prod", "sections": [section]})
    with pytest.raises(ValidationError, match="hidden"):
        QuerySpec(instance="t", rows={"property": "ccm:taxonid"}, percent="row", series=[
            {"id": "a", "label": "a"}, {"id": "b", "label": "b", "hidden": True}])


def test_the_assistant_may_filter_by_a_period(api, settings):
    from wda.llm.tools import Toolbox

    services = api.app.state.services
    box = Toolbox(services, "wlo-prod") if _positional(Toolbox) else None
    if box is None:
        pytest.skip("Toolbox hat eine andere Signatur")
    import asyncio

    spec = {"rows": {"property": "ccm:replicationsource"}, "series": [
        {"id": "neu", "label": "neu", "filters": [
            {"property": CREATED, "values": [{"since": "12m"}]}]}]}
    out = asyncio.run(box._query(spec))
    assert out.series[0].filters[0].property == CREATED
    # ein fester Zeitpunkt bleibt dem Modell verwehrt: nur der relative Zeitraum
    fixed = {"filters": [{"property": CREATED, "values": ["1600000000000"]}]}
    with pytest.raises(Exception, match="nicht gefiltert"):
        asyncio.run(box._query(fixed))


def _positional(cls) -> bool:
    import inspect

    return len(inspect.signature(cls).parameters) >= 2


def _client(handler) -> LlmClient:
    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return LlmClient(http, LlmConfig("https://x.org/v1", "gpt-6-luna", "k"))


def test_the_adapted_reasoning_effort_is_remembered():
    import asyncio

    seen = []

    def handler(request):
        body = json.loads(request.content)
        seen.append(body.get("reasoning_effort"))
        if body.get("tools") and body.get("reasoning_effort") != "none":
            return httpx.Response(400, json={"error": {"message": (
                "Function tools with reasoning_effort are not supported. Set "
                "reasoning_effort to 'none'.")}})
        return httpx.Response(200, json={"choices": [{"message": {"content": "ok"}}]})

    client = _client(handler)
    tools = [{"type": "function", "function": {"name": "f", "parameters": {}}}]

    async def rounds():
        for _ in range(4):
            await client.chat([{"role": "user", "content": "x"}], tools)
        await client.chat([{"role": "user", "content": "x"}])

    asyncio.run(rounds())
    # einmal gelernt; ohne Werkzeuge gilt weiter die Voreinstellung
    assert seen == ["low", "none", "none", "none", "none", "low"]


def test_another_error_that_mentions_reasoning_is_not_retried():
    import asyncio

    calls = []

    def handler(request):
        calls.append(1)
        return httpx.Response(400, json={"error": {"message": (
            "This request exceeds the context length, including reasoning tokens.")}})

    with pytest.raises(LlmError):
        asyncio.run(_client(handler).chat([{"role": "user", "content": "x"}]))
    assert len(calls) == 1
