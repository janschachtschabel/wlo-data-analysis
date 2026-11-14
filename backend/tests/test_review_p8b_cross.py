"""Befunde aus dem Querreview 8b: vocab "inside" (Nullzeilen) gegen Filter und top."""
import httpx
from fastapi.testclient import TestClient

from wda.main import create_app

from .conftest import ADMIN_PASSWORD
from .esdata import DISC, DataRepo, make_docs
from .test_engine import TAX


def client_for(settings, repo):
    app = create_app(settings, transport=httpx.MockTransport(repo.handler))
    client = TestClient(app)
    client.post("/api/auth/login", json={"password": ADMIN_PASSWORD})
    return client


def query(api, **spec):
    r = api.post("/api/query", json={"instance": "wlo-prod", **spec})
    assert r.status_code == 200, r.text
    return r.json()


def test_k2_inside_keeps_the_filter_on_the_row_field(settings):
    """Filter auf dem Zeilenfeld: nur die gefilterten Werte (so sagt es der Hinweis, so ist es
    ohne vocab). Mit vocab inside kommen die übrigen Werte der Werteliste als „Nullzeilen“
    zurück, tragen aber die ungefilterten Zahlen der Facette."""
    api = client_for(settings, DataRepo())
    math = DISC + "380"
    flt = [{"property": TAX, "values": [math]}]
    plain = query(api, filters=flt, rows={"property": TAX})
    assert [r["key"] for r in plain["rows"]] == [math]
    inside = query(api, filters=flt, rows={"property": TAX, "vocab": "inside"})
    # HEAD: Physik 122, Geschichte 107, Mathematik 106, … bei „Gesamt“ 106
    assert [r["key"] for r in inside["rows"]] == [math], \
        [(r["label"], r["values"]) for r in inside["rows"]]


def test_g_unused_note_only_speaks_of_rows_that_are_shown(settings):
    docs = [d for d in make_docs() if DISC + "460" not in d.get(TAX, [])]  # kein Physik
    api = client_for(settings, DataRepo(docs))
    res = query(api, rows={"property": TAX, "vocab": "inside", "top": 3})
    notes = {n["code"]: n["text"] for n in res["provenance"]["notes"]}
    assert "rows_unused" not in notes or any(r["values"][0] == 0 for r in res["rows"]), notes
