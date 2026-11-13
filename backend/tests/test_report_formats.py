import csv
import io
import json

import pytest

from wda.report.build import build_report
from wda.report.charts import chart_for
from wda.report.formats import (
    MAX_PDF_CELLS,
    ReportTooLarge,
    pdf_available,
    render,
    to_csv,
    to_html,
    to_json,
    to_markdown,
    to_pdf,
)
from wda.report.model import Column, Failure, Report, Row, Table, Text
from wda.report.text import markdown_to_html

from .test_matrix import matrix, run_template, template
from .test_runner import World

EVIL = '=HYPERLINK("http://böse.example";"klick")'
SCRIPT = "<script>alert(1)</script>"


def sample(view="table"):
    table = Table(
        "t1", f"Fächer {SCRIPT}",
        [Column("anzahl", "Anzahl | gesamt"), Column("anteil", "Anteil", derived=True)],
        [Row("g", "MINT", "group", [1500, 62.5]),
         Row("m", EVIL, "value", [1234567, 51.44], level=1),
         Row("p", SCRIPT, "value", [None, None], level=1),
         Row("x", "+49 Rest", "value", [-5, 0.5], level=1),
         Row("__missing__", "ohne Wert", "missing", [0, 0.0]),
         Row("__total__", "Gesamt", "total", [2400, 100.0])],
        view={"type": view, "options": {}},
        header_groups=[("Block <b>", 2)],
        notes=[{"code": "x", "text": f"Hinweis mit {SCRIPT}"}],
        sources=[{"label": "wlo-prod", "kind": "query", "base_url": "https://r.example",
                  "fetched_at": 1763063415.0, "queries": 3, "filters": "ccm:x = A, B"},
                 {"label": "Mundo", "kind": "manual", "as_of": "2025-10-01", "note": "Vorlage"},
                 {"label": "Kaputt", "kind": "failed"}])
    return Report(
        title=f"Bericht {SCRIPT}", kind="template", run_id="run-1", definition_id="def-1",
        status="partial", started_at="2026-11-13T19:50:15.857Z",
        finished_at="2026-11-13T19:50:16.000Z",
        blocks=[Text("intro", f"# Einleitung\n\nText mit **fett**, *kursiv* und {SCRIPT}\n\n"
                              "- eins\n- zwei <img src=x onerror=alert(1)>\n"),
                table, Failure("f", "Kachel @home", f"Fehler {SCRIPT}", "unavailable")],
        notes=[{"code": "placeholder_unknown", "text": "Platzhalter {{x}} unbekannt."}])


def test_markdown_subset_escapes_everything_else():
    html = markdown_to_html("# Titel\n\nAbsatz **fett** und *kursiv*\nzweite Zeile\n\n"
                            "- a\n- b\n\n<script>x</script> [link](javascript:alert(1))")
    assert "<h2>Titel</h2>" in html and "<strong>fett</strong>" in html
    assert "<em>kursiv</em>" in html and "<ul><li>a</li><li>b</li></ul>" in html
    assert "<script>" not in html and "&lt;script&gt;" in html
    assert "<a " not in html and "href" not in html
    assert markdown_to_html("2 * 3 * 4 und a*b*c") == "<p>2 * 3 * 4 und a*b*c</p>"


def test_json_is_complete_and_strict():
    data = json.loads(to_json(sample()))
    assert data["title"].startswith("Bericht") and data["status"] == "partial"
    assert [b["type"] for b in data["blocks"]] == ["text", "table", "failure"]
    table = data["blocks"][1]
    assert table["rows"][2]["values"] == [None, None]
    assert table["header_groups"] == [["Block <b>", 2]]
    assert table["columns"][1] == {"id": "anteil", "label": "Anteil", "derived": True}


def test_csv_defuses_formulas_and_keeps_numbers_raw():
    raw = to_csv(sample())
    assert raw.startswith(b"\xef\xbb\xbf")
    rows = list(csv.reader(io.StringIO(raw.decode("utf-8-sig")), delimiter=";"))
    flat = [cell for row in rows for cell in row]
    assert "'  " + EVIL in flat and not any(c.lstrip().startswith("=") for c in flat)
    assert "'+49 Rest" in "".join(flat) or "'  +49 Rest" in flat
    assert "'@home" not in flat and "Kachel @home" in flat  # beginnt nicht mit @
    line = next(r for r in rows if r and r[0].endswith(EVIL))
    assert line[1:] == ["1234567", "51,44"]
    assert next(r for r in rows if r and SCRIPT in r[0] and len(r) == 3)[1:] == ["", ""]
    assert next(r for r in rows if r and "Rest" in r[0])[1:] == ["-5", "0,5"]
    assert ["", "Block <b>", ""] in rows
    assert any(r[:1] == ["Quelle"] and "Handeintrag, Stand 2025-10-01" in r[1] for r in rows)
    assert any(r[:1] == ["Fehler"] for r in rows)
    comma = list(csv.reader(io.StringIO(to_csv(sample(), ",").decode("utf-8-sig"))))
    assert next(r for r in comma if r and r[0].endswith(EVIL))[1:] == ["1234567", "51.44"]


def test_csv_defuses_cells_starting_with_at():
    report = sample()
    report.blocks[2].title = "@SUM(A1)"
    flat = [c for row in csv.reader(io.StringIO(to_csv(report).decode("utf-8-sig")),
                                    delimiter=";") for c in row]
    assert "'@SUM(A1)" in flat


def test_markdown_output():
    text = to_markdown(sample()).decode()
    assert text.startswith("# Bericht \\<script\\>")
    assert "| **MINT** | 1.500 | 62,50 |" in text
    assert "1.234.567 | 51,44 |" in text and "| – | – |" in text
    assert "Anzahl \\| gesamt" in text and "Anteil (berechnet)" in text
    assert "**Fehler:**" in text and "Handeintrag, Stand 2025-10-01 (Vorlage)" in text
    assert "abgerufen 13.11.2025" in text or "abgerufen" in text
    table_lines = [line for line in text.splitlines() if line.startswith("|")]
    assert len({line.count("|") - line.count("\\|") for line in table_lines}) == 1


def test_html_escapes_everything_and_loads_nothing():
    html = to_html(sample()).decode()
    assert "<script" not in html
    assert html.count("&lt;script&gt;") >= 5
    assert "<img" not in html and "http-equiv" not in html
    for needle in ('src="http', "href=", "@import", "url("):
        assert needle not in html
    assert '<th class="block" colspan="2">Block &lt;b&gt;</th>' in html
    assert '<tr class="group"><td class="label l0">MINT</td><td>1.500</td>' in html
    assert "<td>–</td><td>–</td>" in html
    assert "<strong>fett</strong>" in html and "<em>kursiv</em>" in html
    assert 'class="failure"' in html and "Kursiv gesetzte Spalten" in html


@pytest.mark.parametrize("view", ["bar", "stacked_bar", "pie", "line"])
def test_charts_are_plain_svg(view):
    report = sample(view)
    svg = chart_for(report.blocks[1])
    assert svg.startswith("<svg") and svg.endswith("</svg>")
    assert "<script" not in svg and "href" not in svg and "&lt;script&gt;" in svg
    assert "NaN" not in svg and "nan" not in svg and "inf" not in svg
    assert svg in to_html(report).decode()


def test_charts_with_nothing_to_draw():
    empty = Table("t", "Leer", [Column("a", "A")], [Row("__total__", "Gesamt", "total", [0])],
                  view={"type": "pie", "options": {}})
    for view in ("bar", "stacked_bar", "pie", "line", "table", "number", "unbekannt"):
        empty.view = {"type": view, "options": {}}
        assert chart_for(empty) is None


needs_pdf = pytest.mark.skipif(not pdf_available(), reason="Pango fehlt auf diesem System")


@needs_pdf
def test_pdf_from_the_same_html():
    pdf = to_pdf(sample("bar"))
    assert pdf.startswith(b"%PDF") and len(pdf) > 3000


@needs_pdf
def test_pdf_renderer_never_fetches(monkeypatch):
    import wda.report.formats as formats
    real = formats.to_html
    monkeypatch.setattr(formats, "to_html", lambda report: real(report).replace(
        b"</body>", b'<img src="https://example.org/x.png"><img src="file:///etc/passwd">'
                    b'<link rel="stylesheet" href="https://example.org/x.css"></body>'))
    opened = []
    import urllib.request
    monkeypatch.setattr(urllib.request, "urlopen",
                        lambda *a, **k: opened.append(a) or (_ for _ in ()).throw(OSError()))
    monkeypatch.setattr(urllib.request.OpenerDirector, "open",
                        lambda self, *a, **k: opened.append(a) or (_ for _ in ()).throw(
                            OSError()))
    assert to_pdf(sample()).startswith(b"%PDF")
    assert opened == []  # abgelehnt, bevor irgendetwas geöffnet wird


def test_pdf_refuses_reports_that_are_too_large():
    report = sample()
    table = report.tables[0]
    table.rows = [Row(f"r{n}", f"R{n}", "value", [1] * len(table.columns))
                  for n in range(MAX_PDF_CELLS // len(table.columns) + 1)]
    with pytest.raises(ReportTooLarge):
        to_pdf(report)


def test_list_items_may_continue_on_the_next_line():
    html = markdown_to_html(
        "Vorweg\n\n- erster Punkt, der\n  umbricht\n- zweiter\n  auch\n\nDanach")
    assert html.count("<ul>") == 1 and html.count("<li>") == 2
    assert "<li>erster Punkt, der umbricht</li>" in html and "<p>Danach</p>" in html


def test_only_indented_lines_continue_a_list_item():
    assert markdown_to_html("- a\nText danach") == "<ul><li>a</li></ul>\n<p>Text danach</p>"


def test_builtin_method_text_is_one_list():
    import json as _json
    from importlib import resources
    data = _json.loads(resources.files("wda.builtin").joinpath(
        "oer-statistik.json").read_text("utf-8"))
    [method] = [s["markdown"] for d in data["definitions"]
                for s in d["spec"].get("sections", []) if s.get("id") == "methodik"]
    html = markdown_to_html(method)
    assert html.count("<ul>") == 1 and "<p>" not in html
    assert html.count("<li>") == method.count("\n- ")


def test_markdown_table_survives_control_characters():
    report = sample()
    report.tables[0].rows[0].label = "A\rB\u2028C"
    text = to_markdown(report).decode("utf-8")
    assert "A B C" in text and "\r" not in text and "\u2028" not in text


def test_real_run_in_all_formats(tmp_path):
    world = World(tmp_path)
    section = matrix()
    section["blocks"][1]["filters"] = [{"property": "ccm:educationalcontext",
                                        "values": ["Hochschle"]}]
    run = run_template(world, template(
        {"id": "text", "type": "text", "markdown": "## Methodik\n\nStand {{run.date}}."},
        section,
        {"id": "bild", "type": "chart", "title": "Lizenzen", "view": {"type": "pie"},
         "query": {"instance": "a", "rows": {"property": "ccm:commonlicense_key"}}}))
    report = build_report(world.go(world.runner.get_run(run["id"])))
    world.services.db.close()
    formats = ("json", "csv", "md", "html") + (("pdf",) if pdf_available() else ())
    outputs = {fmt: render(report, fmt) for fmt in formats}
    assert all(outputs.values())
    html = outputs["html"].decode()
    assert "Sekundarstufe I" in html and "<svg" in html and "Hochschule: " in html
    assert "<h3>Methodik</h3>" in html
    md = outputs["md"].decode()
    assert "## Methodik" in md and "Mundo: Handeintrag" in md and "**MINT**" in md
    with pytest.raises(ValueError):
        render(report, "docx")


def test_wide_matrix_is_split_by_blocks_not_cut_off():
    columns = [Column(f"b{b}.s{n}", f"S{n}") for b in range(7) for n in range(6)]
    table = Table("m", "Breit", columns,
                  [Row("r", "Zeile", "value", list(range(42))),
                   Row("__total__", "Gesamt", "total", list(range(42)))],
                  header_groups=[(f"Block {b}", 6) for b in range(7)])
    report = sample()
    report.blocks = [table]
    html = to_html(report).decode()
    assert html.count("<table") == 4 and html.count(">Zeile</td>") == 4
    for b in range(7):
        assert html.count(f'colspan="6">Block {b}</th>') == 1
    for value in range(42):
        assert html.count(f"<td>{value}</td>") == 2  # Zeile und Gesamtzeile, je einmal
    # schmale Tabellen und Tabellen ohne Blöcke bleiben ganz
    assert to_html(sample()).decode().count("<table") == 1
