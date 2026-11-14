"""Word-Export: eine gültige Datei mit Titel, Texten, Tabellen und Hinweisen."""
import io
import zipfile
from xml.etree import ElementTree

from wda.report.docx import to_docx
from wda.report.model import Column, Failure, Report, Row, Table, Text

W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"


def document(*blocks, notes=()):
    report = Report(title="Bericht & <Stand>", kind="dashboard", run_id="r", definition_id=None,
                    status="ok", started_at="", finished_at="", blocks=list(blocks),
                    notes=list(notes))
    archive = zipfile.ZipFile(io.BytesIO(to_docx(report, "Stand heute", lambda _t: ["Quelle A"])))
    assert archive.testzip() is None
    assert {"[Content_Types].xml", "_rels/.rels", "word/document.xml",
            "word/styles.xml"} <= set(archive.namelist())
    return ElementTree.fromstring(archive.read("word/document.xml"))  # noqa: S314


def texts(root):
    return ["".join(t.text or "" for t in p.iter(W + "t")) for p in root.iter(W + "p")]


def test_title_text_table_and_notes():
    table = Table(key="t", title="Fächer", columns=[Column("a", "Anzahl"), Column("b", "Frei")],
                  rows=[Row("k", "Mathe\x01matik", "value", [1234, 7], percent=[None, 0.6]),
                        Row("__total__", "Gesamt", "total", [2000, None])],
                  notes=[{"code": "x", "text": "Ein Hinweis"}])
    root = document(Text("e", "# Überblick\n\nErster **Absatz**.\n\n- Punkt eins"), table,
                    Failure("f", "Kaputt", "ging nicht", "upstream"),
                    notes=[{"code": "n", "text": "Zum Bericht"}])
    found = texts(root)
    assert found[0] == "Bericht & <Stand>" and "Stand heute" in found
    assert "Überblick" in found and "Erster Absatz." in found and "• Punkt eins" in found
    assert "Fächer" in found and "Mathematik" in found
    assert "Hinweis: Ein Hinweis" in found and "Quelle: Quelle A" in found
    assert "Kaputt" in found and "Fehler: ging nicht" in found
    assert "Hinweis: Zum Bericht" in found
    # eine Tabelle mit Kopf und zwei Zeilen; Zahlen in deutscher Schreibweise, Lücke als „–“
    [grid] = list(root.iter(W + "tbl"))
    rows = [["".join(t.text or "" for t in cell.iter(W + "t")) for cell in row.iter(W + "tc")]
            for row in grid.iter(W + "tr")]
    assert rows[0] == ["", "Anzahl", "Frei"]
    assert rows[1] == ["Mathematik", "1.234", "7 (0,6 %)"]
    assert rows[2] == ["Gesamt", "2.000", "–"]


def test_wide_tables_are_split_and_topics_listed():
    columns = [Column(f"c{n}", f"Spalte {n}") for n in range(14)]
    topics = [[{"label": "Bruchrechnung", "count": 5, "share": 50.0}]] + [[] for _ in range(13)]
    table = Table(key="t", title="Breit", columns=columns,
                  rows=[Row("k", "Zeile", "value", list(range(14)), topics=topics)])
    root = document(table)
    grids = list(root.iter(W + "tbl"))
    assert len(grids) >= 2  # in Teilen, damit die Seite sie fasst
    assert any("Bruchrechnung" in line for line in texts(root))
