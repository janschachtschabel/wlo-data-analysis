"""Auswertungen mit Bezug zu den Quellen: Merkmale je Quelle (profile) und Quellen nach
Größe (sizes)."""

import pytest
from pydantic import ValidationError

from wda.engine.formula import QueryError
from wda.engine.spec import QuerySpec

from .esdata import SOURCES, DataRepo, make_docs
from .test_engine import LIC, SRC, TAX, query

PRICE, PUB, LANG = "ccm:price", "ccm:oeh_publisher_combined", "cclom:general_language"


@pytest.fixture
def repo():
    return DataRepo()


def notes_of(result):
    return {n["code"]: n["text"] for n in result["provenance"]["notes"]}


def table(result):
    ids = [c["id"] for c in result["columns"]]
    return {r["key"]: dict(zip(ids, r["values"], strict=True)) for r in result["rows"]}


def test_profile_counts_a_facet_only_field_per_source(repo, tmp_path):
    result = query(repo, tmp_path, rows={"property": SRC, "top": 10},
                   profile={"property": PRICE, "show": "count"})
    ids = [c["id"] for c in result["columns"]]
    assert ids[0] == "count" and set(ids[1:]) == {"no", "yes", "yes_for_additional",
                                                  "__missing__"}
    got = table(result)
    assert set(got) == set(SOURCES)
    for src, row in got.items():
        docs = [d for d in repo.docs if src in d[SRC]]
        assert row["count"] == len(docs)
        for value in ("no", "yes", "yes_for_additional"):
            assert row[value] == sum(1 for d in docs if value in d[PRICE])
        assert row["__missing__"] == sum(1 for d in docs if not d[PRICE])
    # die Grundgesamtheit, die Werte des Merkmals und je Zeile eine Abfrage
    assert result["provenance"]["queries"] == 2 + len(SOURCES)
    assert not any(c["derived"] for c in result["columns"][:-1])
    totals = {c["id"]: c["total"] for c in result["columns"]}
    assert totals["count"] == len(repo.docs)
    assert totals["no"] == sum(1 for d in repo.docs if "no" in d[PRICE])


def test_profile_as_share_of_each_row(repo, tmp_path):
    result = query(repo, tmp_path, rows={"property": SRC, "top": 2},
                   profile={"property": PRICE})
    assert len(result["rows"]) == 2 and "rows_top" in notes_of(result)
    assert all(c["label"].endswith("%") and c["derived"] for c in result["columns"][1:])
    for src, row in table(result).items():
        docs = [d for d in repo.docs if src in d[SRC]]
        assert row["count"] == len(docs)
        assert row["yes"] == round(100 * sum(1 for d in docs if "yes" in d[PRICE]) / len(docs), 2)
        assert sum(v for k, v in row.items() if k != "count") == pytest.approx(100, abs=0.05)


def test_profile_of_a_multi_valued_field_has_no_missing_column(repo, tmp_path):
    result = query(repo, tmp_path, rows={"property": PUB, "top": 5},
                   profile={"property": LANG, "show": "count", "top": 2})
    ids = [c["id"] for c in result["columns"]]
    assert len(ids) == 3 and "__missing__" not in ids
    assert "profile_multi" in notes_of(result) and "columns_top" in notes_of(result)
    for pub, row in table(result).items():
        for value in ids[1:]:
            assert row[value] == sum(1 for d in repo.docs
                                     if pub in d[PUB] and value in d[LANG])


def test_profile_respects_filters_and_include(repo, tmp_path):
    math = "http://w3id.org/openeduhub/vocabs/discipline/380"
    result = query(repo, tmp_path, filters=[{"property": TAX, "values": ["Mathematik"]}],
                   rows={"property": SRC, "include": ["bpb_spider"]},
                   profile={"property": PRICE, "show": "count"})
    docs = [d for d in repo.docs if "bpb_spider" in d[SRC] and math in d[TAX]]
    row = table(result)["bpb_spider"]
    assert row["count"] == len(docs) and row["no"] == sum(1 for d in docs if "no" in d[PRICE])


def test_profile_row_is_unknown_when_the_row_criterion_counts_something_else(repo, tmp_path):
    # der Lizenzschlüssel trifft als Präfix: "CC_BY" zählt auch "CC_BY_SA"
    result = query(repo, tmp_path, rows={"property": LIC, "top": 20},
                   profile={"property": PRICE, "show": "count"})
    got = table(result)
    assert got["CC_BY"]["count"] == sum(1 for d in repo.docs if d[LIC] == ["CC_BY"])
    assert got["CC_BY"]["no"] is None and "profile_inexact" in notes_of(result)
    assert got["CC_0"]["no"] == sum(1 for d in repo.docs if d[LIC] == ["CC_0"]
                                    and "no" in d[PRICE])


def test_profile_spec_rules():
    for bad in ({"profile": {"property": PRICE}},  # ohne Zeilen
                {"profile": {"property": PRICE}, "rows": {"property": SRC}},  # ohne Grenze
                {"profile": {"property": PRICE}, "rows": {"property": SRC, "top": 200}},
                {"profile": {"property": PRICE}, "rows": {"property": SRC, "top": 5},
                 "columns": {"property": TAX}},
                {"profile": {"property": PRICE}, "rows": {"property": SRC, "top": 5},
                 "fill": {"fields": [{"property": TAX}]}},
                {"profile": {"property": PRICE},
                 "rows": {"property": SRC, "top": 5, "missing": True}},
                {"profile": {"property": PRICE}, "rows": {"property": TAX, "rollup": "top"}},
                {"profile": {"property": PRICE}, "rows": {"property": SRC, "top": 5},
                 "percent": "row"}):
        with pytest.raises(ValidationError):
            QuerySpec(instance="t", **bad)


def test_profile_rejects_same_field_and_closed_fields(repo, tmp_path):
    with pytest.raises(QueryError, match="verschiedene"):
        query(repo, tmp_path, rows={"property": SRC, "top": 5}, profile={"property": SRC})
    before = len(repo.searches)
    with pytest.raises(Exception, match="cclom:title"):
        query(repo, tmp_path, rows={"property": SRC, "top": 5},
              profile={"property": "cclom:title"})
    assert len(repo.searches) == before  # nichts gefragt
    with pytest.raises(QueryError, match="Filtern"):
        query(repo, tmp_path, filters=[{"property": SRC, "values": ["bpb_spider"]}],
              rows={"property": SRC, "top": 5}, profile={"property": PRICE})


# ---- Größenklassen ------------------------------------------------------------------

def sized_repo():
    docs = make_docs(60)
    sizes = {"gross": 30, "mittel": 12, "klein-a": 3, "klein-b": 2, "einzel-a": 1,
             "einzel-b": 1, "einzel-c": 1}
    names = [name for name, n in sizes.items() for _ in range(n)]
    for n, doc in enumerate(docs):
        doc[PUB] = [names[n]] if n < len(names) else []
    return DataRepo(docs), sizes


def test_sizes_counts_values_and_materials_per_class(tmp_path):
    repo, sizes = sized_repo()
    result = query(repo, tmp_path, sizes={"property": PUB, "bounds": [1, 2, 5, 20]})
    assert [c["id"] for c in result["columns"]] == ["values", "values_share", "uses",
                                                    "uses_share"]
    assert [(r["key"], r["label"]) for r in result["rows"]] == [
        ("1", "1"), ("2-4", "2 bis 4"), ("5-19", "5 bis 19"), ("20+", "ab 20")]
    got = table(result)
    assert got["1"] == {"values": 3, "values_share": round(300 / 7, 2), "uses": 3,
                        "uses_share": 6.0}
    assert got["2-4"]["values"] == 2 and got["2-4"]["uses"] == 5
    assert got["5-19"]["values"] == 1 and got["20+"]["uses"] == 30
    totals = [c["total"] for c in result["columns"]]
    assert totals == [7, None, 50, None]
    assert result["provenance"]["queries"] == 1 and "sizes" in notes_of(result)


def test_sizes_respects_filters_and_default_bounds(repo, tmp_path):
    result = query(repo, tmp_path, filters=[{"property": SRC, "values": ["bpb_spider"]}],
                   sizes={"property": PUB})
    assert [r["key"] for r in result["rows"]] == ["1", "2-4", "5-19", "20-99", "100-999",
                                                  "1000+"]
    docs = [d for d in repo.docs if "bpb_spider" in d[SRC]]
    assert sum(r["values"][2] for r in result["rows"]) == sum(len(d[PUB]) for d in docs)
    assert sum(r["values"][0] for r in result["rows"]) == len({v for d in docs for v in d[PUB]})


def test_sizes_spec_rules(repo, tmp_path):
    for bad in ({"sizes": {"property": PUB, "bounds": [2, 5]}},      # beginnt nicht bei 1
                {"sizes": {"property": PUB, "bounds": [1, 5, 5]}},
                {"sizes": {"property": PUB, "bounds": [1, 9, 3]}},
                {"sizes": {"property": PUB}, "rows": {"property": SRC}},
                {"sizes": {"property": PUB}, "series": [{"id": "a", "label": "A"}]},
                {"sizes": {"property": PUB}, "profile": {"property": PRICE}}):
        with pytest.raises(ValidationError):
            QuerySpec(instance="t", **bad)
    with pytest.raises(QueryError, match="Filtern"):
        query(repo, tmp_path, filters=[{"property": PUB, "values": ["bpb"]}],
              sizes={"property": PUB})


# ---- Dashboard „Quellen“ -------------------------------------------------------------

def test_sources_dashboard_is_valid_and_fits_the_grid():
    from wda.api.builtin import bundles
    from wda.store.specs import parse_spec

    entry = bundles()["quellen"]["definitions"][0]
    assert entry["id"] == "builtin-quellen"
    parsed = parse_spec("dashboard", entry["spec"])
    cells, kinds = set(), set()
    for tile in parsed.tiles:
        q = tile.query
        assert q is not None and q.instance == "wlo-prod", tile.id
        kinds.add("sizes" if q.sizes else "profile" if q.profile else "fill" if q.fill
                  else "matrix" if q.columns else "rows")
        # jede Kachel hat einen Bezug zu den Quellen: Zeilen, Größen, Bindung oder der
        # Filter auf die Quelldatensätze
        fields = {q.rows.property if q.rows else None, q.sizes.property if q.sizes else None,
                  *(f.property for f in (q.fill.fields if q.fill else []))}
        assert fields & {SRC, PUB} or q.filters, tile.id
        if q.profile:  # nur Felder, deren Facette live gemessen ist
            assert q.profile.property in {
                PRICE, "ccm:containsAdvertisement", "ccm:conditionsOfAccess",
                "ccm:dataProtectionConformity", "ccm:fskRating", "ccm:editorial_checklist",
                "ccm:oeh_lrt_aggregated", LANG}, tile.id
        assert tile.layout.x + tile.layout.w <= 12
        area = {(x, y) for x in range(tile.layout.x, tile.layout.x + tile.layout.w)
                for y in range(tile.layout.y, tile.layout.y + tile.layout.h)}
        assert not area & cells, tile.id
        cells |= area
    assert kinds == {"sizes", "profile", "fill", "matrix", "rows"}


def test_sources_dashboard_runs_and_counts_per_source(settings):
    import httpx
    from fastapi.testclient import TestClient

    from wda.main import create_app

    from .conftest import ADMIN_PASSWORD

    repo = DataRepo()
    with TestClient(create_app(settings, transport=httpx.MockTransport(repo.handler))) as api:
        assert api.post("/api/auth/login", json={"password": ADMIN_PASSWORD}).status_code == 204
        assert api.post("/api/builtin/quellen/install").status_code == 200
        run = api.post("/api/definitions/builtin-quellen/run").json()
        failed = [(r["key"], r["error"]) for r in run["results"] if r["error"]]
        assert not failed, failed
        results = {r["key"]: r["result"] for r in run["results"]}
        docs = repo.docs
        kosten = table(results["kosten-je-bezugsquelle"])
        for pub, row in kosten.items():
            having = [d for d in docs if pub in d[PUB]]
            assert row["count"] == len(having)
            assert row["no"] == round(100 * sum(1 for d in having if "no" in d[PRICE])
                                      / len(having), 2)
        sizes = results["groessen-bezugsquellen"]
        assert sizes["columns"][0]["total"] == len({v for d in docs for v in d[PUB]})
        offen = table(results["offenheit-je-bezugsquelle"])
        row = offen["bpb"]
        assert row["anteil"] == round(row["offen"] / row["gesamt"] * 100, 1)
        missing = table(results["ohne-bezugsquelle"])
        for src, row in missing.items():
            assert row[list(row)[1]] == sum(1 for d in docs if src in d[SRC] and not d[PUB])
        # der Testbestand kennt keine Quelldatensätze: leer, aber kein Fehler
        assert results["quelldatensaetze"]["base_total"] == 0
        report = api.get(f"/api/runs/{run['id']}/report").json()
        assert len(report["blocks"]) == len(run["results"])
        html = api.get(f"/api/runs/{run['id']}/export?format=html").text
        assert "Kosten je Bezugsquelle" in html
        assert api.get(f"/api/runs/{run['id']}/export?format=csv").status_code == 200
