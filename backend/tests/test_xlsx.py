"""Excel-Export: lesbar auch mit schwierigen Zeichen, Namen und Spaltenblöcken."""
import io
import zipfile
from xml.etree import ElementTree

from wda.report.model import Column, Report, Row, Table
from wda.report.xlsx import to_xlsx

NS = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"


def book(*tables):
    report = Report(title="Bericht", kind="dashboard", run_id="r", definition_id=None,
                    status="ok", started_at="", finished_at="", blocks=list(tables))
    return zipfile.ZipFile(io.BytesIO(to_xlsx(report, "Stand", lambda _table: [])))


def table(title, label="Zeile", groups=None):
    return Table(key=title, title=title, columns=[Column("a", "Anzahl"), Column("b", "Anzahl")],
                 rows=[Row("k", label, "value", [1, 2])], header_groups=groups)


def texts(archive, name):
    root = ElementTree.fromstring(archive.read(name))  # noqa: S314 - selbst erzeugt
    return [node.text for node in root.iter(NS + "t")]


def test_forbidden_characters_do_not_break_the_file():
    archive = book(table("T", label="a￾b￿c\ud800d\x01e"))
    assert "abcde" in texts(archive, "xl/worksheets/sheet2.xml")


def test_sheet_names_are_valid_and_unique():
    archive = book(table("Übersicht"), table("History"), table("B" * 27 + "'x"),
                   table("B" * 27 + "'y"))
    root = ElementTree.fromstring(archive.read("xl/workbook.xml"))  # noqa: S314
    names = [sheet.get("name") for sheet in root.iter(NS + "sheet")]
    assert names[0] == "Übersicht" and len(set(n.casefold() for n in names)) == len(names)
    assert "History" not in names
    assert all(len(n) <= 31 and not n.endswith(("'", " ")) and not n.startswith("'")
               for n in names)


def test_column_blocks_are_part_of_the_header():
    archive = book(table("T", groups=[("WLO", 1), ("OERSI", 1)]))
    found = texts(archive, "xl/worksheets/sheet2.xml")
    assert "WLO · Anzahl" in found and "OERSI · Anzahl" in found
