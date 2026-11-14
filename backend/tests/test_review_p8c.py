"""Befunde aus dem Review von Paket 8c als Tests."""
import json

import httpx
import pytest
from fastapi.testclient import TestClient

from wda.main import create_app
from wda.report import heat
from wda.report.build import table_from_result

from .conftest import ADMIN_PASSWORD
from .esdata import DataRepo, make_docs
from .test_engine import LVL, SRC, TAX, query
from .test_llm import Model, call, say

PRICE, PUB = "ccm:price", "ccm:oeh_publisher_combined"


def notes_of(result):
    return {n["code"]: n["text"] for n in result["provenance"]["notes"]}


def table(result):
    ids = [c["id"] for c in result["columns"]]
    return {r["key"]: dict(zip(ids, r["values"], strict=True)) for r in result["rows"]}


class Capped(DataRepo):
    """Das Repositorium kürzt die Werteliste (facetLimit 1 heißt hier 5 Werte)."""

    def handler(self, request):
        if b"facetLimit" in request.content:
            body = json.loads(request.content)
            body["facetLimit"] = 1
            request = httpx.Request(request.method, request.url, json=body)
        return super().handler(request)


# W1 ----------------------------------------------------------------------------------
def test_w1_dashboard_definition_context_uses_the_instance_of_its_tiles(settings):
    model, repo = Model(), DataRepo()
    app = create_app(settings, transport=httpx.MockTransport(repo.handler),
                     llm_transport=httpx.MockTransport(model.handler))
    with TestClient(app) as api:
        api.post("/api/auth/login", json={"password": ADMIN_PASSWORD})
        made = api.post("/api/definitions", json={"kind": "dashboard", "name": "D", "spec": {
            "tiles": [{"id": "a", "title": "A", "layout": {"x": 0, "y": 0, "w": 12, "h": 1},
                       "query": {"instance": "eigene", "rows": {"property": LVL}}}]}}).json()
        before = len(repo.requests)
        model.answers = [call(("read_run", {}),
                              ("run_query", {"spec": {"rows": {"property": TAX}}})), say("ok")]
        api.post("/api/assistant/chat", json={
            "instance": "wlo-prod", "definition_id": made["id"],
            "messages": [{"role": "user", "content": "Und nach Fach?"}]})
    hosts = {q.url.host for q in repo.requests[before:] if "/search/" in q.url.path}
    assert hosts == {"edu.example.org"}, hosts
    assert "Instanz: eigene" in model.bodies[0]["messages"][0]["content"]


# W2 ----------------------------------------------------------------------------------
def test_w2_missing_is_not_silently_too_low_when_a_single_field_carries_two_values(tmp_path):
    docs = make_docs(400)
    for n, doc in enumerate(docs):
        if n % 10 == 2:
            doc[PRICE] = ["no", "yes"]  # laut Metadatenset einwertig, in den Daten nicht
    repo = DataRepo(docs)
    result = query(repo, tmp_path, rows={"property": SRC, "top": 10},
                   profile={"property": PRICE, "show": "count"})
    truth = {src: sum(1 for d in docs if src in d[SRC] and not d[PRICE])
             for src in table(result)}
    got = {src: row["__missing__"] for src, row in table(result).items()}
    warned = any("höchstens einen Wert" in text or "zu niedrig" in text
                 for text in notes_of(result).values())
    assert got == truth or warned, (got, truth)


# W3 ----------------------------------------------------------------------------------
def test_w3_sizes_of_a_truncated_facet_show_no_shares_of_the_part(tmp_path):
    docs = make_docs(400)
    for n, doc in enumerate(docs):
        doc[PUB] = [f"pub{n % 9}"] if n % 7 else []  # 9 Werte, geliefert werden 5
    result = query(Capped(docs), tmp_path, sizes={"property": PUB, "bounds": [1, 2, 5, 20]})
    assert "facet_truncated" in notes_of(result)
    totals = {c["id"]: c["total"] for c in result["columns"]}
    assert totals["values"] is None                                # heute 5, wahr sind 9
    assert totals["uses"] == sum(len(d[PUB]) for d in docs)        # heute 192, wahr 343
    assert all(r["values"][1] is None and r["values"][3] is None   # heute 100 %
               for r in result["rows"])


# W4 ----------------------------------------------------------------------------------
def test_w4_profile_shares_are_not_coloured_as_a_fill_rate(tmp_path):
    result = query(DataRepo(), tmp_path, rows={"property": SRC, "top": 5},
                   profile={"property": PRICE})
    shown = table_from_result("k", "Kosten", result, {"type": "heatmap"})  # so vom Assistenten
    assert heat.scale_of(shown) == "count"  # heute "rate": „ohne Angabe 57 %“ gelb, ab 80 grün


# G1 ----------------------------------------------------------------------------------
def test_g1_sizes_rejects_search_helpers_like_profile_does(tmp_path):
    from wda.engine.formula import QueryError
    with pytest.raises(QueryError, match="Suchhilfe"):
        query(DataRepo(), tmp_path, sizes={"property": "virtual:kita_search"})


# G2 ----------------------------------------------------------------------------------
def test_g2_unknown_rows_are_explained_by_their_real_cause(tmp_path):
    docs = make_docs(400)
    for n, doc in enumerate(docs):
        doc[PUB] = [f"pub{n % 9}"] if n % 7 else []
    result = query(Capped(docs), tmp_path, rows={"property": PUB, "include": ["pub0", "pub8"]},
                   profile={"property": PRICE, "show": "count"})
    assert table(result)["pub8"]["count"] is None
    assert "Wortanfang" not in notes_of(result).get("profile_inexact", "")


# G3 ----------------------------------------------------------------------------------
def test_g3_column_ids_stay_unique(tmp_path):
    docs = make_docs(60)
    for n, doc in enumerate(docs):
        doc[PRICE] = [["count", "__missing__", "no"][n % 3]]
    result = query(DataRepo(docs), tmp_path, rows={"property": SRC, "top": 3},
                   profile={"property": PRICE, "show": "count"})
    ids = [c["id"] for c in result["columns"]]
    assert len(ids) == len(set(ids)), ids
