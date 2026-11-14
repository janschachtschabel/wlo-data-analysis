"""Kommentar des Assistenten zu einem Bericht: Er bekommt die Zahlen des Berichts und schreibt
einen kurzen Text. Gespeichert wird nichts."""
import json

from .test_definition_routes import QUERY, make
from .test_llm import api, model, repo, say  # noqa: F401


def test_assistant_comments_on_a_stored_run(api, model):  # noqa: F811
    m = make(api, "metric", "Fächer im Bestand", {"query": QUERY})
    run = api.post(f"/api/definitions/{m['id']}/run").json()
    model.answers = [say("Mathematik ist das größte Fach.")]
    out = api.post("/api/assistant/comment", json={"run_id": run["id"]})
    assert out.status_code == 200, out.text
    assert out.json() == {"text": "Mathematik ist das größte Fach.", "truncated": False}
    [body] = model.bodies
    assert "tools" not in body  # er schreibt nur, er zählt und speichert nichts
    sent = json.dumps(body["messages"], ensure_ascii=False)
    assert "Fächer im Bestand" in sent and "Mathematik" in sent and "400" in sent
    # nichts ist gespeichert: weiterhin eine Definition und ein Lauf
    assert len(api.get("/api/definitions").json()) == 1
    assert len(api.get("/api/runs").json()) == 1


def test_comment_errors_are_plain(api, model, settings):  # noqa: F811
    assert api.post("/api/assistant/comment", json={"run_id": "gibt-es-nicht"}).status_code == 404
    m = make(api, "metric", "Fächer", {"query": QUERY})
    run = api.post(f"/api/definitions/{m['id']}/run").json()
    model.answers = [say("   ")]
    empty = api.post("/api/assistant/comment", json={"run_id": run["id"]})
    assert empty.status_code == 502 and empty.json()["kind"] == "llm_bad_answer"
    api.cookies.clear()
    assert api.post("/api/assistant/comment", json={"run_id": run["id"]}).status_code == 401
