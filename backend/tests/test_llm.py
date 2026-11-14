"""Sprachmodell: Einstellungen, Aufruf, Werkzeuge und die Unterhaltung gegen ein gespieltes
Modell. Geprüft wird vor allem, was nie geschehen darf: Schlüssel in Antworten, Datensätze
beim Modell, Schreiben ohne den Admin."""

import asyncio
import json

import httpx
import pytest
from fastapi.testclient import TestClient

from wda.llm.client import LlmClient, LlmError
from wda.llm.config import LlmConfig, check_base_url
from wda.main import create_app

from .conftest import ADMIN_PASSWORD, LLM_KEY
from .esdata import DISC, EC, DataRepo
from .test_engine import LIC, LVL, TAX

NEW_KEY = "sk-neuer-schluessel-987"


class Model:
    """Gespieltes Sprachmodell: gibt der Reihe nach die vorbereiteten Antworten."""

    def __init__(self, *answers):
        self.answers = list(answers)
        self.requests: list[httpx.Request] = []

    @property
    def bodies(self):
        return [json.loads(r.content) for r in self.requests]

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        answer = self.answers.pop(0) if self.answers else say("Fertig.")
        return answer(request) if callable(answer) else answer


def say(text):
    return httpx.Response(200, json={"choices": [{"message": {"role": "assistant",
                                                               "content": text}}]})


def call(*calls):
    return httpx.Response(200, json={"choices": [{"message": {
        "role": "assistant", "content": None,
        "tool_calls": [{"id": f"c{n}", "type": "function",
                        "function": {"name": name, "arguments": json.dumps(args)}}
                       for n, (name, args) in enumerate(calls)]}}]})


@pytest.fixture
def model():
    return Model()


@pytest.fixture
def repo():
    return DataRepo()


@pytest.fixture
def api(settings, model, repo):
    app = create_app(settings, transport=httpx.MockTransport(repo.handler),
                     llm_transport=httpx.MockTransport(model.handler))
    with TestClient(app) as client:
        assert client.post("/api/auth/login",
                           json={"password": ADMIN_PASSWORD}).status_code == 204
        yield client


def chat(api, text="Wie viele Materialien gibt es?", **more):
    r = api.post("/api/assistant/chat", json={
        "instance": "wlo-prod", "messages": [{"role": "user", "content": text}], **more})
    assert r.status_code == 200, r.text
    assert r.headers["content-type"].startswith("application/x-ndjson")
    return [json.loads(line) for line in r.text.splitlines()]


# ---- Einstellungen ---------------------------------------------------------------------

def test_base_url_rules():
    assert check_base_url(" https://api.openai.com/v1/ ") == "https://api.openai.com/v1"
    assert check_base_url("http://localhost:11434/v1") == "http://localhost:11434/v1"
    assert check_base_url("http://ollama:11434/v1")
    for bad in ("http://api.openai.com/v1", "ftp://x/v1", "https://u:p@x.org/v1",
                "https://x.org/v1?a=1", "https://", "x"):
        with pytest.raises(ValueError):
            check_base_url(bad)


def test_effort_follows_the_model_family():
    assert LlmConfig("https://x.org", "gpt-6-luna", "k").reasoning_effort == "low"
    assert LlmConfig("https://x.org", "mistral-large", "k").reasoning_effort is None
    assert LlmConfig("https://x.org", "gpt-6-luna", "k", "off").reasoning_effort is None
    assert LlmConfig("https://x.org", "mistral-large", "k", "high").reasoning_effort == "high"


def test_settings_are_stored_and_never_return_the_key(api, settings):
    r = api.put("/api/settings/llm", json={
        "base_url": "https://b-api.example.org/api/v1/llm/openai/", "model": "gpt-6-luna",
        "api_key": NEW_KEY, "effort": "medium"})
    assert r.status_code == 200, r.text
    expected = {"base_url": "https://b-api.example.org/api/v1/llm/openai",
                "model": "gpt-6-luna", "effort": "medium", "api_key_set": True,
                "ready": True, "stored": True}
    assert r.json() == expected
    assert api.get("/api/settings").json()["llm"] == expected
    for text in (r.text, api.get("/api/settings").text):
        assert NEW_KEY not in text and LLM_KEY not in text
    # ein Neustart liest dieselben Werte
    with TestClient(create_app(settings)) as again:
        again.post("/api/auth/login", json={"password": ADMIN_PASSWORD})
        assert again.get("/api/settings").json()["llm"] == expected
    # zurücksetzen stellt die Umgebung wieder her
    assert api.delete("/api/settings/llm").json() == {
        "base_url": "https://api.openai.com/v1", "model": "gpt-6-luna", "effort": "auto",
        "api_key_set": True, "ready": True, "stored": False}


def test_key_is_kept_for_the_same_host_only(api, model):
    same = api.put("/api/settings/llm", json={"base_url": "https://api.openai.com/v2",
                                              "model": "anderes"})
    assert same.status_code == 200 and same.json()["api_key_set"] is True
    other = api.put("/api/settings/llm", json={"base_url": "https://boese.example.org/v1",
                                               "model": "x"})
    assert other.status_code == 422 and "api_key" in other.json()["detail"]
    probe = api.post("/api/settings/llm/test", json={
        "base_url": "https://boese.example.org/v1", "model": "x"})
    assert probe.status_code == 422
    assert model.requests == []  # der Schlüssel der Umgebung ist nirgends hingegangen
    assert api.get("/api/settings").json()["llm"]["base_url"] == "https://api.openai.com/v2"


def test_invalid_settings_are_rejected_without_echo(api):
    for body in ({"base_url": "http://extern.example.org/v1", "model": "m"},
                 {"base_url": "https://x.org/v1", "model": "mit leerzeichen"},
                 {"base_url": "https://x.org/v1", "model": "m", "effort": "max"},
                 {"base_url": "https://x.org/v1", "model": "m", "api_key": "schlüssel mit ö"},
                 {"base_url": "https://x.org/v1", "model": "m", "extra": 1}):
        r = api.put("/api/settings/llm", json=body)
        assert r.status_code == 422, body
        assert "schlüssel mit" not in r.text


def test_connection_test_reports_success_and_failures(api, model):
    model.answers = [say("bereit")]
    ok = api.post("/api/settings/llm/test").json()
    assert ok["ok"] is True and ok["answer"] == "bereit" and ok["model"] == "gpt-6-luna"
    sent = model.requests[0]
    assert str(sent.url) == "https://api.openai.com/v1/chat/completions"
    assert sent.headers["authorization"] == f"Bearer {LLM_KEY}"
    body = json.loads(sent.content)
    assert body["model"] == "gpt-6-luna" and body["reasoning_effort"] == "low"
    assert "temperature" not in body and "max_tokens" not in body
    # mit noch nicht gespeicherten Werten und neuem Schlüssel
    model.answers = [httpx.Response(401, json={"error": {
        "message": f"Incorrect API key provided: {NEW_KEY}"}})]
    bad = api.post("/api/settings/llm/test", json={
        "base_url": "https://anderswo.example.org/v1", "model": "m", "api_key": NEW_KEY})
    assert bad.json() == {"ok": False, "kind": "llm_auth",
                          "detail": "Der Anbieter des Sprachmodells lehnt den Schlüssel ab."}
    assert model.requests[1].headers["authorization"] == f"Bearer {NEW_KEY}"
    assert api.get("/api/settings").json()["llm"]["stored"] is False  # nichts gespeichert


# ---- Aufruf ----------------------------------------------------------------------------

def run_client(handler, config=None):
    async def go():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            client = LlmClient(http, config or LlmConfig("https://x.org/v1", "gpt-6-luna",
                                                         "geheim"))
            return await client.chat([{"role": "user", "content": "hallo"}])

    return asyncio.run(go())


def test_retries_once_without_reasoning_effort():
    seen = []

    def handler(request):
        body = json.loads(request.content)
        seen.append(body)
        if "reasoning_effort" in body:
            return httpx.Response(400, json={"error": {
                "message": "Unrecognized request argument supplied: reasoning_effort"}})
        return say("ok")

    answer = run_client(handler)
    assert answer["content"] == "ok" and len(seen) == 2 and "reasoning_effort" not in seen[1]


@pytest.mark.parametrize("response, kind", [
    (httpx.Response(403, json={}), "llm_auth"),
    (httpx.Response(404, json={"error": {"message": "model not found"}}), "llm_rejected"),
    (httpx.Response(429, json={}), "llm_unreachable"),
    (httpx.Response(503, text="<html>"), "llm_unreachable"),
    (httpx.Response(302, headers={"location": "https://boese.example.org"}), "llm_unreachable"),
    (httpx.Response(200, text="<html>kein Modell</html>"), "llm_bad_answer"),
    (httpx.Response(200, json={"choices": []}), "llm_bad_answer"),
    (httpx.Response(200, json={"choices": [{"message": {"content": 5}}]}), "llm_bad_answer"),
])
def test_failures_have_a_kind_and_never_the_key(response, kind):
    with pytest.raises(LlmError) as caught:
        run_client(lambda request: response)
    assert caught.value.kind == kind and "geheim" not in caught.value.message


def test_provider_message_is_passed_on_trimmed_and_without_the_key():
    def handler(request):
        return httpx.Response(400, json={"error": {"message": "falsch geheim " + "x" * 900}})

    with pytest.raises(LlmError) as caught:
        run_client(handler, LlmConfig("https://x.org/v1", "mistral", "geheim"))
    assert "geheim" not in caught.value.message and len(caught.value.message) < 400


def test_network_failure_and_missing_key():
    def boom(request):
        raise httpx.ConnectError("nein")

    with pytest.raises(LlmError) as caught:
        run_client(boom)
    assert caught.value.kind == "llm_unreachable"
    with pytest.raises(LlmError) as caught:
        run_client(boom, LlmConfig("https://x.org/v1", "m", None))
    assert caught.value.kind == "llm_not_configured"


def test_oversized_answer_is_refused():
    with pytest.raises(LlmError) as caught:
        run_client(lambda request: httpx.Response(200, content=b"x" * (3 * 1024 * 1024)))
    assert caught.value.kind == "llm_bad_answer"


# ---- Unterhaltung ----------------------------------------------------------------------

def test_plain_answer(api, model):
    model.answers = [say("Das weiß ich nicht.")]
    assert chat(api) == [{"type": "final", "text": "Das weiß ich nicht.",
                          "html": "<p>Das weiß ich nicht.</p>"}]
    body = model.bodies[0]
    assert body["messages"][0]["role"] == "system"
    assert "ccm:taxonid" in body["messages"][0]["content"]  # Felder der Instanz
    assert body["messages"][-1] == {"role": "user", "content": "Wie viele Materialien gibt es?"}
    names = [t["function"]["name"] for t in body["tools"]]
    assert names == ["list_fields", "field_values", "run_query", "propose_view",
                     "propose_dashboard"]


def test_counts_with_the_engine_and_answers(api, model, repo):
    model.answers = [
        call(("run_query", {"spec": {"rows": {"property": TAX, "match": DISC},
                                     "instance": "gibt-es-nicht", "refresh": True}})),
        say("Mathematik liegt vorn."),
    ]
    events = chat(api)
    assert events[0] == {"type": "step", "tool": "run_query",
                         "text": f"Zählt: nach {TAX}"}
    assert events[-1]["text"] == "Mathematik liegt vorn."
    tool = model.bodies[1]["messages"][-1]
    assert tool["role"] == "tool" and tool["tool_call_id"] == "c0"
    result = json.loads(tool["content"])
    assert result["spalten"] == ["Anzahl"]
    rows = dict((label, value) for label, value in result["zeilen"])
    assert rows["Mathematik"] == repo.brute(repo.has(TAX, DISC + "380"))
    assert result["gesamt"] == [len(repo.docs)]
    assert any("mehrere Werte" in note for note in result["hinweise"])


def test_tool_errors_go_back_to_the_model_as_text(api, model):
    model.answers = [
        call(("run_query", {"spec": {"rows": {"property": "cclom:title"}}}),
             ("run_query", {"spec": {"zeilen": 1}}),
             ("run_query", {"spec": "kaputt"}),
             ("field_values", {"property": "gibt:es:nicht"}),
             ("drop_database", {}),
             ("run_query", {"spec": {"filters": [{"property": TAX, "values": ["Mathe"]}]}})),
        say("So geht es nicht."),
    ]
    events = chat(api)
    assert events[-1]["type"] == "final"
    results = [json.loads(m["content"]) for m in model.bodies[1]["messages"]
               if m["role"] == "tool"]
    assert len(results) == 6 and all("fehler" in r for r in results)
    assert "unbekanntes Feld" in results[1]["fehler"]
    assert "gibt es nicht" in results[4]["fehler"]
    assert results[5]["vorschlaege"]  # „Mathe“ ist nicht eindeutig, die Werteliste hilft


def test_broken_arguments_do_not_break_the_conversation(api, model):
    broken = httpx.Response(200, json={"choices": [{"message": {"content": None, "tool_calls": [
        {"id": "a", "function": {"name": "run_query", "arguments": "{kein json"}},
        {"id": "b", "function": {"name": "run_query", "arguments": "[1]"}},
        {"function": None}, "unsinn"]}}]})
    model.answers = [broken, say("Entschuldigung.")]
    events = chat(api)
    assert events[-1]["text"] == "Entschuldigung."
    tools = [m for m in model.bodies[1]["messages"] if m["role"] == "tool"]
    assert len(tools) == 4 and all("fehler" in json.loads(m["content"]) for m in tools)


def test_lookup_tools_show_fields_and_values_but_no_helper_fields(api, model):
    model.answers = [call(("list_fields", {}), ("list_fields", {"search": "bildungsstufe"}),
                          ("field_values", {"property": TAX, "search": "phys"}),
                          ("field_values", {"property": LIC})), say("ok")]
    chat(api)
    all_fields, found, values, free = [
        json.loads(m["content"]) for m in model.bodies[1]["messages"] if m["role"] == "tool"]
    props = [f["property"] for f in all_fields["felder"]]
    assert TAX in props and not any(p.startswith("virtual:") for p in props)
    assert [f["property"] for f in found["felder"]] == [LVL]
    assert values["werte"] == [{"id": DISC + "460", "bezeichnung": "Physik"}]
    assert "keine Werteliste" in free["fehler"]


def test_proposed_view_is_checked_run_and_shown(api, model, repo):
    spec = {"rows": {"property": TAX, "match": DISC},
            "columns": {"property": LVL, "match": EC}}
    model.answers = [call(("propose_view", {"title": "Fach × Stufe", "spec": spec,
                                            "view": "heatmap"})), say("Siehe Karte.")]
    events = chat(api)
    assert [e["type"] for e in events] == ["step", "proposal", "final"]
    proposal = events[1]
    assert proposal["kind"] == "metric" and proposal["name"] == "Fach × Stufe"
    assert proposal["spec"]["view"] == {"type": "heatmap"}
    assert proposal["spec"]["query"]["instance"] == "wlo-prod"
    table = proposal["report"]["blocks"][0]
    assert table["type"] == "table" and table["view"]["type"] == "heatmap"
    assert len(table["rows"]) >= 5
    # der Vorschlag lässt sich unverändert über die bestehende Route speichern
    saved = api.post("/api/definitions", json={
        "kind": "metric", "name": proposal["name"], "spec": proposal["spec"]})
    assert saved.status_code == 201, saved.text
    # nichts wurde ohne den Admin angelegt
    assert len(api.get("/api/definitions").json()) == 1


def test_proposed_dashboard_gets_a_layout_and_failing_tiles_are_reported(api, model):
    good = {"title": "Fächer", "spec": {"rows": {"property": TAX}}, "view": "bar",
            "width": "half"}
    model.answers = [
        call(("propose_dashboard", {"name": "Überblick", "tiles": [
            good, {"title": "kaputt", "spec": {"rows": {"property": "cclom:title"}}}]})),
        call(("propose_dashboard", {"name": "Überblick", "description": "Bestand im Blick",
                                    "tiles": [good, dict(good, title="Stufen",
                                                         spec={"rows": {"property": LVL}}),
                                              {"title": "Füllgrade", "spec": {"fill": {
                                                  "fields": [{"property": LIC}]}}}]})),
        say("Angelegt werden kann es mit einem Klick."),
    ]
    events = chat(api)
    assert [e["type"] for e in events] == ["step", "step", "proposal", "final"]
    first = json.loads(model.bodies[1]["messages"][-1]["content"])
    assert "kaputt" in first["fehler"]
    proposal = events[2]
    assert proposal["kind"] == "dashboard" and proposal["description"] == "Bestand im Blick"
    layouts = [t["layout"] for t in proposal["spec"]["tiles"]]
    assert layouts == [{"x": 0, "y": 0, "w": 6, "h": 8}, {"x": 6, "y": 0, "w": 6, "h": 8},
                       {"x": 0, "y": 8, "w": 12, "h": 8}]
    assert api.get("/api/definitions").json() == []
    saved = api.post("/api/definitions", json={
        "kind": "dashboard", "name": proposal["name"], "spec": proposal["spec"]})
    assert saved.status_code == 201, saved.text


def test_rounds_and_tool_calls_are_limited(api, model):
    loop = call(("list_fields", {}), ("list_fields", {}), ("list_fields", {}))
    model.answers = [loop] * 20
    events = chat(api)
    assert events[-1]["type"] == "final"
    assert sum(1 for e in events if e["type"] == "step") == 16
    assert len(model.requests) <= 9
    last = model.bodies[-1]
    assert "tools" not in last and last["messages"][-1]["role"] == "user"


def test_provider_failure_in_the_middle_becomes_an_event(api, model):
    model.answers = [call(("list_fields", {})), httpx.Response(500, text="kaputt")]
    events = chat(api)
    assert events[0]["type"] == "step"
    assert events[-1]["type"] == "error" and events[-1]["kind"] == "llm_unreachable"


def test_history_and_memo_reach_the_model(api, model):
    model.answers = [say("Gern.")]
    r = api.post("/api/assistant/chat", json={"instance": "wlo-prod", "messages": [
        {"role": "user", "content": "Zeig mir Fächer"},
        {"role": "assistant", "content": "Hier.", "memo": "Fächer: {\"rows\": 1}"},
        {"role": "user", "content": "Und als Balken?"}]})
    assert r.status_code == 200
    sent = model.bodies[0]["messages"]
    assert [m["role"] for m in sent] == ["system", "user", "assistant", "user"]
    assert "Dem Nutzer gezeigte Vorschläge: Fächer" in sent[2]["content"]


def test_requests_are_checked_before_the_stream_starts(api, model):
    def post(**body):
        return api.post("/api/assistant/chat", json=body)

    q = [{"role": "user", "content": "x"}]
    assert post(instance="gibt-es-nicht", messages=q).status_code == 404
    assert post(instance="wlo-prod", messages=[]).status_code == 422
    assert post(instance="wlo-prod", messages=[{"role": "assistant", "content": "x"}]
                ).status_code == 422
    assert post(instance="wlo-prod", messages=[{"role": "system", "content": "x"}]
                ).status_code == 422
    assert post(instance="wlo-prod", messages=[{"role": "user", "content": "x" * 4001}]
                ).status_code == 422
    assert post(instance="wlo-prod", messages=q * 31).status_code == 422
    assert post(instance="wlo-prod", messages=q, run_id="gibt-es-nicht").status_code == 404
    assert model.requests == []


def test_without_a_key_the_assistant_says_so(settings, repo, model):
    settings.llm_api_key = None
    app = create_app(settings, transport=httpx.MockTransport(repo.handler),
                     llm_transport=httpx.MockTransport(model.handler))
    with TestClient(app) as client:
        client.post("/api/auth/login", json={"password": ADMIN_PASSWORD})
        r = client.post("/api/assistant/chat", json={
            "instance": "wlo-prod", "messages": [{"role": "user", "content": "x"}]})
        assert r.status_code == 409 and r.json()["kind"] == "llm_not_configured"
        assert client.get("/api/settings").json()["llm"]["ready"] is False
        test = client.post("/api/settings/llm/test").json()
        assert test["ok"] is False and test["kind"] == "llm_not_configured"
    assert model.requests == []


def test_assistant_requires_login(client):
    r = client.post("/api/assistant/chat", json={
        "instance": "wlo-prod", "messages": [{"role": "user", "content": "x"}]})
    assert r.status_code == 401
    assert client.put("/api/settings/llm", json={}).status_code == 401


def test_chat_about_a_run_reads_its_tables(api, model, repo):
    made = api.post("/api/definitions", json={"kind": "metric", "name": "Fächer", "spec": {
        "query": {"instance": "wlo-prod", "rows": {"property": TAX}}}}).json()
    run = api.post(f"/api/definitions/{made['id']}/run", json={}).json()
    model.answers = [call(("read_run", {})), say("Mathematik ist am größten.")]
    events = chat(api, "Was fällt auf?", run_id=run["id"])
    assert events[0] == {"type": "step", "tool": "read_run",
                         "text": "Liest die Tabellen der Auswertung"}
    assert "read_run" in [t["function"]["name"] for t in model.bodies[0]["tools"]]
    assert "read_run" in model.bodies[0]["messages"][0]["content"]
    data = json.loads(model.bodies[1]["messages"][-1]["content"])
    assert data["auswertung"] == "Fächer"
    rows = dict((row[0], row[1]) for row in data["tabellen"][0]["zeilen"])
    assert rows["Mathematik"] == repo.brute(repo.has(TAX, DISC + "380"))


def test_nothing_the_model_receives_contains_records_or_secrets(api, model, repo):
    """Alles, was an das Modell geht, sind Felder, Wertelisten und Zählungen."""
    model.answers = [call(("list_fields", {}), ("field_values", {"property": TAX}),
                          ("run_query", {"spec": {"rows": {"property": TAX}}}),
                          ("run_query", {"spec": {"fill": {"fields": [{"property": LIC}]}}})),
                     say("ok")]
    chat(api)
    sent = b"".join(r.content for r in model.requests).decode()
    for secret in (ADMIN_PASSWORD, LLM_KEY, "instanz-geheimnis-456", "nutzer-789"):
        assert secret not in sent
    for word in ("nodes", "ref", "downloadUrl", "preview"):
        assert f'"{word}"' not in sent


def test_answer_is_rendered_without_letting_html_through(api, model):
    model.answers = [say("**Wichtig:** <script>alert(1)</script>\n\n- eins\n- zwei")]
    final = chat(api)[-1]
    assert final["html"] == ("<p><strong>Wichtig:</strong> &lt;script&gt;alert(1)&lt;/script&gt;"
                             "</p>\n<ul><li>eins</li><li>zwei</li></ul>")


def test_chat_about_a_definition_counts_it_fresh_without_saving(api, model, repo):
    made = api.post("/api/definitions", json={"kind": "metric", "name": "Stufen", "spec": {
        "query": {"instance": "wlo-prod", "rows": {"property": LVL}}}}).json()
    model.answers = [call(("read_run", {})), say("ok")]
    chat(api, "Was fällt auf?", definition_id=made["id"])
    data = json.loads(model.bodies[1]["messages"][-1]["content"])
    assert data["auswertung"] == "Stufen" and data["tabellen"][0]["zeilen"]
    assert api.get("/api/runs").json() == []  # nichts gespeichert
    r = api.post("/api/assistant/chat", json={
        "instance": "wlo-prod", "definition_id": "gibt-es-nicht",
        "messages": [{"role": "user", "content": "x"}]})
    assert r.status_code == 404
