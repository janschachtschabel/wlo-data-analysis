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
                "ready": True, "stored": True, "locked": False}
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
        "api_key_set": True, "ready": True, "stored": False, "locked": False}


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


def test_function_tools_need_reasoning_effort_none():
    """Live gesehen (OpenAI, 2026-11-14): Mit Werkzeugen lehnt das Modell jede Stufe außer
    ``none`` ab, auch die eigene Voreinstellung."""
    seen = []

    def handler(request):
        body = json.loads(request.content)
        seen.append(body.get("reasoning_effort", "fehlt"))
        if body.get("reasoning_effort") != "none":
            return httpx.Response(400, json={"error": {"message": (
                "Function tools with reasoning_effort are not supported for gpt-6-luna in "
                "/v1/chat/completions. To use function tools, use /v1/responses or set "
                "reasoning_effort to 'none'.")}})
        return say("ok")

    assert run_client(handler)["content"] == "ok"
    assert seen == ["low", "none"]


def test_a_reasoning_error_that_never_ends_is_reported():
    def handler(request):
        return httpx.Response(400, json={"error": {"message": "reasoning_effort: nein"}})

    with pytest.raises(LlmError) as caught:
        run_client(handler)
    assert caught.value.kind == "llm_rejected"


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
                     "propose_dashboard", "propose_template"]


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
    assert result["spalten"] == ["Zeile", "Anzahl"]
    rows = dict((label, value) for label, value in result["zeilen"])
    assert rows["Mathematik"] == repo.brute(repo.has(TAX, DISC + "380"))
    assert result["gesamt"] == ["Gesamt", len(repo.docs)]
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
    # was kein Objekt ist, geht nicht an den Anbieter zurück
    assert len(tools) == 3 and all("fehler" in json.loads(m["content"]) for m in tools)


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
    # die Vorschau kommt mit: je Kachel eine Tabelle mit ihrer Darstellung
    blocks = proposal["report"]["blocks"]
    assert [b["type"] for b in blocks] == ["table"] * 3
    assert [b["view"]["type"] for b in blocks] == ["bar", "bar", "table"]
    assert [b["key"] for b in blocks] == ["k1", "k2", "k3"]
    told = json.loads(model.bodies[2]["messages"][-1]["content"])
    assert told["darstellungen"] == ["bar", "bar", "table"] and "anlegen" in told["hinweis"]
    assert "nie „erstellt“" in model.bodies[0]["messages"][0]["content"]
    assert api.get("/api/definitions").json() == []
    saved = api.post("/api/definitions", json={
        "kind": "dashboard", "name": proposal["name"], "spec": proposal["spec"]})
    assert saved.status_code == 201, saved.text


def test_rounds_and_tool_calls_are_limited(api, model):
    loop = call(("list_fields", {}), ("list_fields", {}), ("list_fields", {}))
    model.answers = [loop] * 40
    events = chat(api)
    assert events[-1]["type"] == "final"
    assert sum(1 for e in events if e["type"] == "step") == 32
    assert len(model.requests) <= 17
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


def test_without_the_switch_only_the_environment_counts(tmp_path):
    """Ohne ``WDA_LLM_SETTINGS_UI`` gilt allein die Umgebung: Gespeichertes zählt nicht, und
    die App nimmt keine Änderung an."""
    from fastapi.testclient import TestClient

    from wda.config import Settings
    from wda.main import create_app

    from .conftest import ADMIN_PASSWORD

    def app(**extra):
        settings = Settings(admin_password=ADMIN_PASSWORD, cookie_secure=False,
                            data_dir=tmp_path, scheduler=False, **extra)
        client = TestClient(create_app(settings))
        client.__enter__()
        assert client.post("/api/auth/login",
                           json={"password": ADMIN_PASSWORD}).status_code == 204
        return client

    stored = {"base_url": "https://anders.example/v1", "model": "m", "api_key": "aus-der-app"}
    first = app(llm_settings_ui=True)
    assert first.put("/api/settings/llm", json=stored).status_code == 200
    assert first.get("/api/settings").json()["llm"]["locked"] is False
    first.__exit__(None, None, None)

    second = app(llm_api_key="aus-der-umgebung", llm_model="env-modell", llm_effort="off")
    try:
        llm = second.get("/api/settings").json()["llm"]
        assert llm["locked"] and llm["model"] == "env-modell" and not llm["stored"]
        assert llm["effort"] == "off" and "aus-der" not in json.dumps(llm)
        assert second.put("/api/settings/llm", json=stored).status_code == 409
        assert second.delete("/api/settings/llm").status_code == 409
        assert second.app.state.services.llm.api_key == "aus-der-umgebung"
    finally:
        second.__exit__(None, None, None)


def test_a_dashboard_preview_too_large_for_the_chat_falls_back_to_titles(api, model, monkeypatch):
    from wda.llm import tools
    monkeypatch.setattr(tools, "MAX_PREVIEW_CHARS", 200)
    model.answers = [
        call(("propose_dashboard", {"name": "Groß", "tiles": [
            {"title": "Fächer", "spec": {"rows": {"property": TAX}}, "view": "bar"}]})),
        say("Die Vorschau ist zu groß."),
    ]
    events = chat(api)
    proposal = next(e for e in events if e["type"] == "proposal")
    assert proposal["report"] is None and proposal["spec"]["tiles"]
    told = json.loads(model.bodies[1]["messages"][-1]["content"])
    assert "zu groß" in told["hinweis"]


def test_a_step_that_overruns_the_time_is_stopped(api, model, monkeypatch):
    import asyncio

    from wda.llm import assistant
    from wda.llm.tools import Toolbox

    async def slow(self, name, args):
        await asyncio.sleep(5)
        return "{}"

    monkeypatch.setattr(Toolbox, "call", slow)
    monkeypatch.setattr(assistant, "MAX_SECONDS", 0.05)
    monkeypatch.setattr(assistant, "MIN_STEP_SECONDS", 0.05)
    model.answers = [call(("list_fields", {})), say("Soweit.")]
    events = chat(api)
    assert events[-1] == {**events[-1], "type": "final", "text": "Soweit."}
    assert "zu lange" in model.bodies[1]["messages"][-2]["content"]


# ---- Template-Vorschlag ----------------------------------------------------------------

def _template_sections():
    return [
        {"type": "text",
         "markdown": "# Bestand\n\nStand {{run.date}}, gesamt {{a2.total.alle.gesamt}}. "
                     "<img src=x onerror=alert(1)>"},
        {"type": "matrix", "title": "Fächer nach Gruppen", "rows": {"property": TAX, "groups": [
            {"label": "MINT", "values": ["Mathematik", DISC + "460"]},
            {"label": "Sprachen", "values": ["Deutsch"]}]},
         "columns": [{"id": "gesamt", "label": "Gesamt"},
                     {"id": "doppelt", "label": "Doppelt", "formula": "gesamt * 2"}],
         "blocks": [{"label": "Alle"}, {"label": "Hochschule", "filters": [
             {"property": LVL, "values": [EC + "hochschule"]}]}]},
        {"type": "table", "title": "Stufen", "spec": {"rows": {"property": LVL}}},
        {"type": "chart", "title": "Fächer", "spec": {"rows": {"property": TAX, "top": 5}},
         "view": "bar"},
    ]


def test_proposed_template_carries_the_report_and_saves_nothing(api, model):
    model.answers = [call(("propose_template", {
        "name": "Bestandsbericht", "description": "Fächer und Stufen",
        "sections": _template_sections()})), say("Als Vorschau, Knopf „Template anlegen“.")]
    events = chat(api)
    assert [e["type"] for e in events] == ["step", "proposal", "final"]
    assert "Bestandsbericht" in events[0]["text"]
    proposal = events[1]
    assert proposal["kind"] == "template" and proposal["name"] == "Bestandsbericht"
    spec = proposal["spec"]
    assert spec["instance"] == "wlo-prod" and spec["title"] == "Bestandsbericht"
    assert [(s["id"], s["type"]) for s in spec["sections"]] == [
        ("a1", "text"), ("a2", "matrix"), ("a3", "table"), ("a4", "chart")]
    matrix = spec["sections"][1]
    assert [(g["id"], g["label"]) for g in matrix["rows"]["sections"]] == [
        ("mint", "MINT"), ("sprachen", "Sprachen")]
    # Namen sind aufgelöst: In der Definition stehen die Werte selbst
    assert matrix["rows"]["sections"][0]["values"] == [
        {"value": DISC + "380", "literal": True}, {"value": DISC + "460", "literal": True}]
    assert [(b["id"], b["source"]) for b in matrix["blocks"]] == [
        ("alle", "query"), ("hochschule", "query")]
    assert spec["sections"][3]["view"] == {"type": "bar"}
    blocks = proposal["report"]["blocks"]
    assert [b["type"] for b in blocks] == ["text", "table", "table", "table"]
    table = blocks[1]
    assert [r["kind"] for r in table["rows"]][:3] == ["group", "value", "value"]
    assert [c["id"] for c in table["columns"]] == [
        "alle.gesamt", "alle.doppelt", "hochschule.gesamt", "hochschule.doppelt"]
    total = next(r for r in table["rows"] if r["kind"] == "total")
    assert total["values"][0] > 0 and total["values"][1] == 2 * total["values"][0]
    # der Platzhalter im Text ist mit der gezählten Zahl gefüllt
    assert "{{" not in blocks[0]["markdown"]
    assert "<h2>Bestand</h2>" in blocks[0]["html"] and "<img" not in blocks[0]["html"]
    told = json.loads(model.bodies[1]["messages"][-1]["content"])
    assert told["abschnitte"] == 4 and "anlegen" in told["hinweis"]
    assert "a2.total.alle.gesamt" in json.dumps(told["platzhalter"])
    # nichts wurde angelegt; der Vorschlag lässt sich unverändert speichern
    assert api.get("/api/definitions").json() == []
    saved = api.post("/api/definitions", json={
        "kind": "template", "name": proposal["name"], "spec": proposal["spec"]})
    assert saved.status_code == 201, saved.text


@pytest.mark.parametrize(("change", "expected"), [
    (lambda s: s[1]["rows"]["groups"][0]["values"].append("Gibtsnichtkunde"), "Gibtsnichtkunde"),
    (lambda s: s[1]["rows"].update(property="cclom:title"), "cclom:title"),
    (lambda s: s[1]["blocks"][1].update(filters=[{"property": "cclom:title",
                                                  "values": ["x"]}]), "cclom:title"),
    (lambda s: s[1]["blocks"].append({"label": "Hand", "source": "manual"}), "Abfrage"),
    (lambda s: s[1]["columns"][1].update(formula="__import__('os')"), "Abschnitt 2"),
    (lambda s: s[1]["rows"]["groups"].append({"label": "Leer", "values": []}), "Leer"),
    (lambda s: s.append({"type": "bild", "title": "x"}), "Abschnitt 5"),
    (lambda s: s[2].update(spec={"rows": {"property": "cclom:title"}}), "Stufen"),
    (lambda s: s.extend([{"type": "text", "markdown": "x"}] * 20), "Höchstens"),
    (lambda s: s.clear(), "mindestens"),
])
def test_template_proposals_with_mistakes_come_back_as_text(api, model, change, expected):
    sections = _template_sections()
    change(sections)
    model.answers = [call(("propose_template", {"name": "B", "sections": sections})),
                     say("Geht so nicht.")]
    events = chat(api)
    assert [e["type"] for e in events] == ["step", "final"]
    told = json.loads(model.bodies[1]["messages"][-1]["content"])
    assert expected in told["fehler"], told
    assert api.get("/api/definitions").json() == []


def test_template_proposals_share_the_limit_and_the_guide_explains_them(api, model):
    one = ("propose_template", {"name": "B", "sections": _template_sections()[2:3]})
    model.answers = [call(one), call(one), call(one), say("Genug.")]
    events = chat(api)
    assert [e["type"] for e in events].count("proposal") == 2
    assert "höchstens" in json.loads(model.bodies[3]["messages"][-1]["content"])["fehler"]
    guide = model.bodies[0]["messages"][0]["content"]
    assert "propose_template" in guide and "Template anlegen" in guide
    assert "groups" in guide and "Handeintrag" in guide
    names = [t["function"]["name"] for t in model.bodies[0]["tools"]]
    assert "propose_template" in names
