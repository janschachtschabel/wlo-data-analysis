"""Eingefärbte Tabelle: dieselben Fälle wie frontend/src/app/shared/heat.spec.ts."""

from wda.report import heat
from wda.report.formats import to_html
from wda.report.model import Column, Report, Row, Table


def table(columns, rows, view=None):
    return Table("k", "T", [Column(i, label, derived) for i, label, derived in columns],
                 [Row(f"{kind}{n}", f"Zeile {n}", kind, values)
                  for n, (kind, values) in enumerate(rows)],
                 view=view or {"type": "heatmap", "options": {}})


def test_counts_step_by_square_root_zero_is_a_gap_unknown_stays_plain():
    t = table([("a", "A", False), ("b", "B", False)],
              [("value", [100, 25]), ("value", [0, None]), ("value", [1, 4]),
               ("total", [101, 29])])
    assert heat.scale_of(t) == "count"
    assert heat.classes(t) == [["h6", "h3"], ["gap", ""], ["h1", "h2"], ["", ""]]


def test_total_and_missing_rows_do_not_set_the_scale():
    t = table([("a", "A", False)], [("value", [10]), ("total", [1000]), ("missing", [500])])
    assert heat.classes(t) == [["h6"], [""], [""]]


def test_rates_are_a_traffic_light_and_the_count_stays_plain():
    t = table([("count", "Anzahl", False), ("f", "Fach %", True)],
              [("value", [900, 12.5]), ("value", [5, 79.9]), ("value", [5, 80]),
               ("value", [0, None])])
    assert heat.scale_of(t) == "rate"
    assert heat.classes(t) == [["", "r-low"], ["", "r-mid"], ["", "r-high"], ["", ""]]


def test_scale_follows_the_columns_unless_given():
    assert heat.scale_of(table([("count", "Anzahl", False),
                                ("f", "Fach: ohne Wert", True)], [])) == "count"
    assert heat.scale_of(table([("x", "X", False)], [],
                               {"type": "heatmap", "options": {"scale": "rate"}})) == "rate"
    assert heat.scale_of(table([("count", "Anzahl", False)], [])) == "count"


def test_html_export_shades_only_heatmaps():
    def html(view):
        t = table([("a", "A", False)], [("value", [4]), ("value", [0])], view)
        report = Report(title="R", run_id="r", definition_id=None, kind="metric", status="ok",
                        started_at="2026-11-14T00:00:00Z", finished_at="2026-11-14T00:00:01Z",
                        blocks=[t])
        return to_html(report).decode()

    shaded = html({"type": "heatmap", "options": {}})
    assert '<td class="h6">4</td>' in shaded and '<td class="gap">0</td>' in shaded
    assert "0 (Lücke)" in shaded
    plain = html({"type": "table", "options": {}})
    assert '<td class="h6">' not in plain and "0 (Lücke)" not in plain


def test_count_column_beside_others_is_never_shaded_and_fill_tables_shade_the_rate():
    t = table([("count", "Anzahl", False), ("f", "Fach: ohne Wert", True)],
              [("value", [9000, 30]), ("value", [50, 0])])
    assert heat.classes(t) == [["", "h6"], ["", "gap"]]
    per_field = table([("filled", "mit Wert", False), ("missing", "ohne Wert", True),
                       ("rate", "Füllgrad %", True)], [("value", [10, 90, 10.0])])
    assert heat.scale_of(per_field) == "rate"
    assert heat.classes(per_field) == [["", "", "r-low"]]
