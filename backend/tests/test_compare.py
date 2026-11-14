"""„Was hat sich seit dem letzten Lauf geändert?“ – aus den Punkten zweier Läufe."""

import json

from wda.report.compare import delta_text

from .esdata import DISC
from .test_definition_routes import QUERY, TAX, make
from .test_history import api, local_day, repo, run  # noqa: F401

M = DISC + "380"


def rows_of(report, block=0):
    return {r["key"]: r for r in report["blocks"][block]["rows"]}


def test_delta_text():
    assert [delta_text(v, p) for v, p in [(12, 10), (7, 10), (5, 5), (1.5, 1.0), (None, 3),
                                          (3, None), (1200, 200)]] == [
        "+2", "−3", "±0", "+0,50", "", "", "+1.000"]


def test_compare_with_previous_run(api, repo):  # noqa: F811
    m = make(api, "metric", "Fächer", {"query": QUERY})
    first = run(api, m["id"])
    plain = api.get(f"/api/runs/{first['id']}/report").json()
    assert plain["compare"] is None and "previous" in rows_of(plain)["__total__"]
    assert rows_of(plain)["__total__"]["previous"] is None
    none = api.get(f"/api/runs/{first['id']}/report", params={"compare": "previous"})
    assert (none.status_code, none.json()["kind"]) == (404, "not_found")

    repo.docs.extend(repo.docs[:10])
    second = run(api, m["id"], refresh=True)
    report = api.get(f"/api/runs/{second['id']}/report", params={"compare": "previous"}).json()
    assert report["compare"] == {"run_id": first["id"], "started_at": first["started_at"]}
    total = rows_of(report)["__total__"]
    assert (total["values"], total["previous"]) == ([410], [400])
    math_before = rows_of(plain)[M]["values"]
    assert rows_of(report)[M]["previous"] == math_before
    # ausdrücklich genannter Lauf und Datum
    by_id = api.get(f"/api/runs/{second['id']}/report", params={"compare": first["id"]}).json()
    assert by_id["compare"]["run_id"] == first["id"]
    day = local_day(first["started_at"])
    by_day = api.get(f"/api/runs/{second['id']}/report", params={"compare": day}).json()
    assert by_day["compare"]["run_id"] == first["id"]
    assert api.get(f"/api/runs/{second['id']}/report",
                   params={"compare": "1999-01-01"}).status_code == 404
    assert api.get(f"/api/runs/{second['id']}/report",
                   params={"compare": "gibtsnicht"}).status_code == 404
    assert api.get(f"/api/runs/{second['id']}/report",
                   params={"compare": second["id"]}).status_code == 422


def test_changed_definition_new_and_dropped_rows(api):  # noqa: F811
    m = make(api, "metric", "Fächer", {"query": {**QUERY, "filters": [
        {"property": TAX, "values": [M, DISC + "120"]}]}})
    first = run(api, m["id"])
    before = {r["key"] for r in first["results"][0]["result"]["rows"]}
    api.put(f"/api/definitions/{m['id']}", json={"version": 1, "spec": {"query": {
        **QUERY, "filters": [{"property": TAX, "values": [M, DISC + "460"]}]}}})
    second = run(api, m["id"])
    after = {r["key"] for r in second["results"][0]["result"]["rows"]}
    assert after - before and before - after  # die Zeilen haben sich wirklich geändert
    report = api.get(f"/api/runs/{second['id']}/report", params={"compare": "previous"}).json()
    rows = rows_of(report)
    fresh = next(iter(after - before))
    assert rows[fresh]["new"] is True and rows[fresh]["previous"] == [None]
    assert rows[M]["new"] is False
    codes = [n["code"] for n in report["notes"]]
    assert "compare_definition_changed" in codes
    table_codes = {n["code"]: n["text"] for n in report["blocks"][0]["notes"]}
    assert "compare_rows_dropped" in table_codes
    assert str(len(before - after)) in table_codes["compare_rows_dropped"]

    other = make(api, "metric", "Andere", {"query": QUERY})
    foreign = run(api, other["id"])
    mixed = api.get(f"/api/runs/{second['id']}/report", params={"compare": foreign["id"]}).json()
    assert "compare_other_definition" in [n["code"] for n in mixed["notes"]]
    staging = run(api, m["id"], instance="wlo-staging")
    # „voriger Lauf“ meint dieselbe Instanz
    assert api.get(f"/api/runs/{staging['id']}/report",
                   params={"compare": "previous"}).status_code == 404
    cross = api.get(f"/api/runs/{staging['id']}/report", params={"compare": second["id"]}).json()
    assert "compare_other_instance" in [n["code"] for n in cross["notes"]]


def test_exports_show_the_change_only_when_asked(api, repo):  # noqa: F811
    m = make(api, "metric", "Fächer", {"query": QUERY})
    run(api, m["id"])
    repo.docs.extend(repo.docs[:10])
    second = run(api, m["id"], refresh=True)
    url = f"/api/runs/{second['id']}/export"
    for fmt in ("html", "md", "csv", "json"):
        assert "(+10)" not in api.get(url, params={"format": fmt}).text
    html = api.get(url, params={"format": "html", "compare": "previous"}).text
    assert "410 (+10)" in html and "Vergleich mit dem Lauf vom" in html
    md = api.get(url, params={"format": "md", "compare": "previous"}).text
    assert "410 (+10)" in md and "Vergleich mit dem Lauf vom" in md
    csv = api.get(url, params={"format": "csv", "compare": "previous"}).text
    header = next(line for line in csv.splitlines() if "(vorher)" in line)
    assert header.count("(vorher)") == 1
    total = next(line for line in csv.splitlines() if line.startswith("Gesamt"))
    assert total.split(";")[1:3] == ["410", "400"]
    data = json.loads(api.get(url, params={"format": "json", "compare": "previous"}).content)
    assert data["compare"]["run_id"] and rows_of(data)["__total__"]["previous"] == [400]
    assert api.get(url, params={"format": "html", "compare": "1999-01-01"}).status_code == 404
