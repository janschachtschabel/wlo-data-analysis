"""Füllgrade: wie oft ein Feld gefüllt ist, insgesamt und je Zeile, gegen rohe Zählung."""

import json

import pytest
from pydantic import ValidationError

from wda.edusharing.errors import EduSharingError
from wda.engine.formula import QueryError
from wda.engine.spec import QuerySpec

from .esdata import DataRepo
from .test_engine import HS, LIC, LVL, SRC, TAX, M, query


@pytest.fixture
def repo():
    return DataRepo()


def fields(*props):
    return {"fields": [{"property": p} for p in props]}


def has(doc, prop):
    return bool(doc.get(prop))


def test_fill_per_field_matches_brute_count(repo, tmp_path):
    result = query(repo, tmp_path, fill=fields(TAX, LVL, LIC, SRC))
    total = len(repo.docs)
    assert result["total_row"] is False and result["base_total"] == total
    assert [c["id"] for c in result["columns"]] == ["filled", "missing", "rate"]
    assert [c["derived"] for c in result["columns"]] == [False, True, True]
    assert [r["key"] for r in result["rows"]] == [TAX, LVL, LIC, SRC]
    assert [r["label"] for r in result["rows"]][:3] == ["Fach", "Bildungsstufe", "Lizenz"]
    for row in result["rows"]:
        filled = sum(1 for d in repo.docs if has(d, row["key"]))
        assert row["values"] == [filled, total - filled, round(100 * filled / total, 1)]
        assert row["kind"] == "value"
    # die Lizenz ist nicht überall gesetzt, sonst prüft der Test nichts
    lic = next(r for r in result["rows"] if r["key"] == LIC)
    assert 0 < lic["values"][1] < total
    codes = [n["code"] for n in result["provenance"]["notes"]]
    assert "fill_base" in codes and "fill_derived" in codes
    # je Feld höchstens drei Aufrufe (Werteliste, Probe, Zählung), einer für die Gesamtzahl
    assert result["provenance"]["queries"] <= 1 + 3 * 4


def test_fill_respects_base_filters_and_own_label(repo, tmp_path):
    result = query(repo, tmp_path, filters=[{"property": LVL, "values": [HS]}],
                   fill={"fields": [{"property": LIC, "label": "Lizenzangabe"},
                                    {"property": TAX}, {"property": LVL}]})
    docs = [d for d in repo.docs if HS in d.get(LVL, [])]
    rows = {r["key"]: r for r in result["rows"]}
    assert rows[LIC]["label"] == "Lizenzangabe"
    for prop in (LIC, TAX):
        filled = sum(1 for d in docs if has(d, prop))
        assert rows[prop]["values"][:2] == [filled, len(docs) - filled]
    # das gefilterte Feld selbst: jedes gefilterte Material hat dort einen Wert
    assert rows[LVL]["values"] == [len(docs), 0, 100.0]
    assert "fill_filtered" in [n["code"] for n in result["provenance"]["notes"]]


def test_fill_per_row(repo, tmp_path):
    for show in ("rate", "missing", "filled"):
        result = query(repo, tmp_path, rows={"property": SRC},
                       fill={**fields(TAX, LIC), "show": show})
        assert "total_row" not in result or result["total_row"] is True
        assert [c["id"] for c in result["columns"]] == [
            "count", "ccm_taxonid", "ccm_commonlicense_key"]
        assert [c["derived"] for c in result["columns"]] == [
            False, show != "filled", show != "filled"]
        assert len(result["rows"]) > 1
        for row in result["rows"]:
            docs = [d for d in repo.docs if row["key"] in d.get(SRC, [])]
            expected = [len(docs)]
            for prop in (TAX, LIC):
                filled = sum(1 for d in docs if has(d, prop))
                expected.append({"rate": round(100 * filled / len(docs), 1),
                                 "missing": len(docs) - filled, "filled": filled}[show])
            assert row["values"] == expected, (show, row["label"])
        # Gesamtzeile: über alle Materialien
        total = len(repo.docs)
        filled = sum(1 for d in repo.docs if has(d, LIC))
        assert result["columns"][0]["total"] == total
        assert result["columns"][2]["total"] == {
            "rate": round(100 * filled / total, 1), "missing": total - filled,
            "filled": filled}[show]


def test_fill_per_row_with_top_and_sort(repo, tmp_path):
    result = query(repo, tmp_path, rows={"property": SRC, "top": 2}, fill=fields(LIC))
    assert len(result["rows"]) == 2
    counts = [r["values"][0] for r in result["rows"]]
    assert counts == sorted(counts, reverse=True)


def test_field_without_any_value_and_empty_base(repo, tmp_path):
    for doc in repo.docs:
        doc.pop(SRC, None)
    result = query(repo, tmp_path, fill=fields(SRC, TAX))
    assert result["rows"][0]["values"] == [0, len(repo.docs), 0.0]
    empty = query(repo, tmp_path, filters=[{"property": LVL, "values": [
        {"value": "gibtesnichtxyz", "literal": True}]}], fill=fields(TAX))
    assert empty["base_total"] == 0
    assert empty["rows"][0]["values"] == [0, 0, None]


def test_unknown_when_too_many_values(repo, tmp_path, monkeypatch):
    monkeypatch.setattr("wda.engine.engine.MAX_VALUES", 2)
    result = query(repo, tmp_path, fill=fields(TAX, LIC))
    rows = {r["key"]: r for r in result["rows"]}
    assert rows[TAX]["values"] == [None, None, None]
    assert rows[LIC]["values"][0] is not None  # einwertig: braucht keine Werteliste
    note = next(n for n in result["provenance"]["notes"] if n["code"] == "fill_unknown")
    assert "Fach" in note["text"]


def test_rejections(repo, tmp_path):
    with pytest.raises(EduSharingError, match="nicht als Facette freigegeben"):
        query(repo, tmp_path, rows={"property": "cclom:title"}, fill=fields(TAX))
    with pytest.raises(QueryError, match="Zeilenfeld"):
        query(repo, tmp_path, rows={"property": TAX}, fill=fields(TAX))
    with pytest.raises(QueryError, match="Gruppen"):
        query(repo, tmp_path, fill=fields(LIC),
              rows={"property": TAX, "groups": [{"label": "G", "values": [M]}]})
    for bad in ({"fields": []}, {"fields": [{"property": TAX}, {"property": TAX}]},
                {"fields": [{"property": TAX}], "show": "alles"},
                {"fields": [{"property": f"f{i}"} for i in range(31)]}):
        with pytest.raises(ValidationError):
            QuerySpec(instance="t", fill=bad)
    with pytest.raises(ValidationError):
        QuerySpec(instance="t", fill=fields(TAX), series=[{"id": "a", "label": "A"}])
    with pytest.raises(ValidationError):
        QuerySpec(instance="t", fill=fields(TAX), columns={"property": LVL})
    with pytest.raises(ValidationError):
        QuerySpec(instance="t", fill=fields(TAX), percent="column")


def test_no_facet_on_unreleased_field_is_ever_sent(repo, tmp_path):
    """Ein nicht freigegebenes Feld bleibt unbekannt (damit eine Definition auch auf einer
    Instanz läuft, die das Feld nicht kennt); gefragt wird danach nie."""
    result = query(repo, tmp_path, fill=fields(TAX, "cclom:title", "gibt:esnicht"))
    assert [r["values"][0] is None for r in result["rows"]] == [False, True, True]
    text = next(n["text"] for n in result["provenance"]["notes"] if n["code"] == "fill_closed")
    assert "Titel" in text and "gibt:esnicht" in text
    sent = json.dumps(repo.searches)
    assert "cclom:title" not in sent and "gibt:esnicht" not in sent


def test_fill_metric_through_run_report_history_and_export(settings):
    from fastapi.testclient import TestClient

    from wda.main import create_app

    from .conftest import ADMIN_PASSWORD
    from .test_definition_routes import make

    data = DataRepo()
    with TestClient(create_app(settings, transport=data.transport())) as api:
        api.post("/api/auth/login", json={"password": ADMIN_PASSWORD})
        direct = api.post("/api/query", json={"instance": "wlo-prod", "fill": fields(TAX, LIC)})
        assert direct.status_code == 200, direct.text
        m = make(api, "metric", "Füllgrade", {
            "query": {"instance": "wlo-prod", "fill": fields(TAX, LIC)},
            "view": {"type": "stacked_bar"}})
        first = api.post(f"/api/definitions/{m['id']}/run", json={}).json()
        assert first["status"] == "ok"
        for doc in data.docs[:50]:
            doc.pop(LIC, None)
        second = api.post(f"/api/definitions/{m['id']}/run", json={"refresh": True}).json()

        report = api.get(f"/api/runs/{second['id']}/report?compare=previous").json()
        table = report["blocks"][0]
        assert [r["kind"] for r in table["rows"]] == ["value", "value"]  # keine Gesamtzeile
        lic = next(r for r in table["rows"] if r["key"] == LIC)
        assert lic["previous"][0] > lic["values"][0]
        assert any(n["code"] == "fill_base" for n in table["notes"])

        keys = api.get(f"/api/definitions/{m['id']}/history/keys").json()["blocks"][0]
        assert [r["key"] for r in keys["rows"]] == ["value:" + TAX, "value:" + LIC]
        hist = api.get(f"/api/definitions/{m['id']}/history").json()
        assert [s["label"] for s in hist["series"]] == ["mit Wert", "ohne Wert", "Füllgrad %"]
        assert len(hist["points"]) == 2 and hist["points"][0]["values"][0] is not None

        csv = api.get(f"/api/runs/{second['id']}/export?format=csv").text
        assert "Füllgrad %" in csv and "Gesamt;" not in csv
        assert api.get(f"/api/runs/{second['id']}/export?format=html").status_code == 200
        assert api.get(f"/api/runs/{second['id']}/export?format=md").status_code == 200


# ---- Felder, die das Repositorium nur als Facette zählt (live gemessen) --------------------

PRICE, BUFFET, CHECK, KITA = ("ccm:price", "ccm:oeh_buffet_criteria", "ccm:editorial_checklist",
                              "virtual:kita_search")


def notes_of(result):
    return {n["code"]: n["text"] for n in result["provenance"]["notes"]}


def test_single_valued_field_refused_as_filter_is_summed(repo, tmp_path):
    result = query(repo, tmp_path, fill=fields(PRICE, TAX))
    total = len(repo.docs)
    filled = sum(1 for d in repo.docs if has(d, PRICE))
    assert 0 < filled < total
    assert result["rows"][0]["values"] == [filled, total - filled, round(100 * filled / total, 1)]
    assert "Kosten" in notes_of(result)["fill_summed"]
    # mit Grundfilter: die Summe gilt für die gefilterten Materialien
    result = query(repo, tmp_path, filters=[{"property": LVL, "values": [HS]}],
                   fill=fields(PRICE))
    docs = [d for d in repo.docs if HS in d.get(LVL, [])]
    assert result["rows"][0]["values"][0] == sum(1 for d in docs if has(d, PRICE))


def test_multi_valued_field_refused_as_filter_is_unknown_with_range(repo, tmp_path):
    result = query(repo, tmp_path, fill=fields(BUFFET))
    assert result["rows"][0]["values"] == [None, None, None]
    text = notes_of(result)["fill_range"]
    filled = sum(1 for d in repo.docs if has(d, BUFFET))
    low, high = (int(n) for n in text.split("zwischen ")[1].rstrip(".").split(" und "))
    assert low <= filled <= high and low < high
    assert "fill_summed" not in notes_of(result)


def test_filter_that_does_not_match_exactly_is_not_trusted(repo, tmp_path):
    """Das Kriterium des Feldes zählt etwas anderes als die Facette: nie als Füllgrad."""
    result = query(repo, tmp_path, fill=fields(CHECK))
    filled = sum(1 for d in repo.docs if has(d, CHECK))
    wrong = sum(1 for d in repo.docs if has(d, LVL))
    assert wrong > filled > 0
    got = result["rows"][0]["values"][0]
    assert got != wrong
    # je Material ein Wert im Testbestand, das Feld gilt aber als mehrwertig: Spanne
    assert got is None and str(filled) in notes_of(result)["fill_range"]


def test_virtual_field_is_unknown_not_zero(repo, tmp_path):
    result = query(repo, tmp_path, fill=fields(KITA, TAX))
    assert result["rows"][0]["values"] == [None, None, None]
    assert "Kita" in notes_of(result)["fill_virtual"]
    assert result["rows"][1]["values"][0] is not None
    assert not any(f["property"] == KITA for s in repo.searches for f in s.get("facets") or [])


def test_summed_field_has_no_value_per_row(repo, tmp_path):
    result = query(repo, tmp_path, rows={"property": SRC}, fill=fields(PRICE, LIC))
    total = len(repo.docs)
    filled = sum(1 for d in repo.docs if has(d, PRICE))
    assert result["columns"][1]["total"] == round(100 * filled / total, 1)
    assert all(r["values"][1] is None for r in result["rows"])
    assert all(r["values"][2] is not None for r in result["rows"])
    assert "Je Zeile" in notes_of(result)["fill_summed"]


# ---- aus dem Review ---------------------------------------------------------------------

def test_inexact_filter_inside_the_plausible_range_is_caught_by_the_probe(repo, tmp_path):
    """Liegt die Zählung eines ungenauen Kriteriums zufällig zwischen häufigstem Wert und
    Summe, verrät es die Probe mit dem häufigsten Wert."""
    for n, doc in enumerate(repo.docs):
        doc[CHECK] = ["2", "9", "5"][: 1 + n % 3] if n % 2 == 0 else []
    result = query(repo, tmp_path, fill=fields(CHECK))
    filled = sum(1 for d in repo.docs if d[CHECK])
    assert result["rows"][0]["values"][0] in (None, filled)
    per_row = query(repo, tmp_path, rows={"property": SRC}, fill=fields(CHECK))
    assert all(r["values"][1] is None for r in per_row["rows"])


def test_sum_is_not_used_when_it_exceeds_the_total(repo, tmp_path):
    """Einwertig laut Metadatenset, in den Daten aber mehrere Werte: nie 100 % aus der Summe."""
    for doc in repo.docs:
        doc[PRICE] = ["no", "yes"]
    result = query(repo, tmp_path, fill=fields(PRICE))
    assert result["rows"][0]["values"] == [None, None, None]
    assert "fill_range" in notes_of(result) and "fill_summed" not in notes_of(result)
    # die Annahme steht im Hinweis, wenn summiert wird
    assert "höchstens einen Wert" in notes_of(query(DataRepo(), tmp_path, fill=fields(PRICE))
                                              )["fill_summed"]


def test_search_helper_license_is_unknown_not_zero(repo, tmp_path, monkeypatch):
    from . import esdata
    widgets = [*esdata.MDS["widgets"], {"id": "license", "caption": "Lizenzgruppe",
                                        "type": "singleoption",
                                        "values": [{"id": "OPEN", "caption": "offen"}]}]
    monkeypatch.setitem(esdata.MDS, "widgets", widgets)
    result = query(repo, tmp_path, fill=fields("license", LIC))
    assert result["rows"][0]["values"] == [None, None, None]
    assert "Lizenzgruppe" in notes_of(result)["fill_virtual"]


def test_blank_values_do_not_count_as_filled(repo, tmp_path):
    for n, doc in enumerate(repo.docs):
        if n % 4 == 0:
            doc[TAX] = [""]
    result = query(repo, tmp_path, fill=fields(TAX))
    filled = sum(1 for d in repo.docs if any(v.strip() for v in d[TAX]))
    assert result["rows"][0]["values"][0] == filled
    assert "Fach" in notes_of(result)["fill_blank"]


def test_field_without_values_says_so(repo, tmp_path):
    for doc in repo.docs:
        doc[TAX] = []
    result = query(repo, tmp_path, fill=fields(TAX))
    assert result["rows"][0]["values"][0] == 0 and "Fach" in notes_of(result)["fill_empty"]


def test_filter_on_the_row_field_shows_only_the_filtered_rows(repo, tmp_path):
    """Ein Dashboard-Filter „Quelle = …“ darf die Kacheln „je Quelle“ nicht scheitern lassen."""
    result = query(repo, tmp_path, filters=[{"property": SRC, "values": ["bpb_spider"]}],
                   rows={"property": SRC}, fill=fields(LIC, TAX))
    assert [r["key"] for r in result["rows"]] == ["bpb_spider"]
    docs = [d for d in repo.docs if "bpb_spider" in d[SRC]]
    row = result["rows"][0]["values"]
    assert row[0] == len(docs) == result["columns"][0]["total"]
    assert row[1] == round(100 * sum(1 for d in docs if has(d, LIC)) / len(docs), 1)
    assert "rows_filtered" in notes_of(result)


def test_access_error_on_the_count_is_not_taken_for_a_field_problem(repo, tmp_path):
    import httpx
    inner = repo.handler

    def handler(request):
        body = json.loads(request.content) if request.content else {}
        if any(c["property"] == TAX for c in body.get("criteria", [])):
            return httpx.Response(403, json={"error": "x", "message": "denied"})
        return inner(request)

    repo.handler = handler
    repo.transport = lambda: httpx.MockTransport(handler)
    with pytest.raises(EduSharingError):
        query(repo, tmp_path, fill=fields(TAX))


def test_result_says_what_charts_may_show(repo, tmp_path):
    assert query(repo, tmp_path, fill=fields(TAX))["fill"] == {"show": "rate", "by_rows": False}
    assert query(repo, tmp_path, rows={"property": SRC}, fill={**fields(TAX), "show": "filled"}
                 )["fill"] == {"show": "filled", "by_rows": True}


def test_report_keeps_charts_of_fill_tables_honest(repo, tmp_path):
    from wda.report.build import table_from_result
    from wda.report.charts import _data, chart_for

    per_field = query(repo, tmp_path, fill=fields(TAX, LIC, BUFFET))
    stacked = table_from_result("k", "T", per_field, {"type": "stacked_bar", "options": {}})
    labels, values, series = _data(stacked)
    assert series == ["mit Wert", "ohne Wert"] and "Buffet-Kriterien" not in labels
    total = len(repo.docs)
    assert all(sum(row) == total for row in values)
    bar = table_from_result("k", "T", per_field, {"type": "bar", "options": {}})
    assert _data(bar)[2] == ["Füllgrad %"]
    # Zahl, Kreis und Linie werden zur Tabelle, mit Hinweis
    for kind in ("number", "pie", "line"):
        table = table_from_result("k", "T", per_field, {"type": kind, "options": {}})
        assert table.view["type"] == "table" and chart_for(table) is None
        assert "view_changed" in [n["code"] for n in table.notes]

    per_row = query(repo, tmp_path, rows={"property": SRC}, fill=fields(TAX, LIC))
    table = table_from_result("k", "T", per_row, {"type": "bar", "options": {}})
    assert _data(table)[2] == ["Fach %", "Lizenz %"]  # nie die Anzahl unter „Füllgrad“
    assert table_from_result("k", "T", per_row, {"type": "stacked_bar", "options": {}}
                             ).view["type"] == "table"
    assert table_from_result("k", "T", per_row, {"type": "heatmap", "options": {}}
                             ).view["type"] == "heatmap"
