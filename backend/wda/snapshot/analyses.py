"""Auswertungen über den lokalen Abzug. Jede liefert eine Tabelle aus Zählungen in der Form
der Abfrage-Engine; einzelne Materialien kommen in keiner vor."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from ..errors import Notes, QueryError
from .store import Snapshot

SCHEMA_VERSION = 1
KINDS = {"source": "Quelle", "publisher": "Bezugsquelle"}
MIN_GROUP = 1
YEAR = 365.25 * 86400

_DUP_URL = ("n.url_key IN (SELECT url_key FROM nodes WHERE url_key IS NOT NULL "
            "GROUP BY url_key HAVING COUNT(*) > 1)")
_CROSS_URL = ("n.url_key IN (SELECT x.url_key FROM nodes x JOIN memberships y "
              "ON y.node = x.id AND y.kind = 'source' WHERE x.url_key IS NOT NULL "
              "GROUP BY x.url_key HAVING COUNT(DISTINCT y.name) > 1)")
_DUP_TITLE = ("n.title_key IN (SELECT title_key FROM nodes WHERE title_key IS NOT NULL "
              "GROUP BY title_key HAVING COUNT(*) > 1)")
_SCORE = ("(n.title_len > 0) + (n.desc_len > 0) + (n.n_keywords > 0) + (n.n_subjects > 0) + "
          "(n.n_levels > 0) + (n.n_types > 0) + n.has_license + (n.n_languages > 0)")


@dataclass(frozen=True)
class Measure:
    id: str
    label: str
    where: str  # SQL-Bedingung über einen Knoten ``n``; ``:now`` ist der Stand des Abzugs


@dataclass(frozen=True)
class Analysis:
    id: str
    title: str
    measures: tuple[Measure, ...]
    rate: bool = False   # Anteile sind Füllgrade (hoch ist gut): Ampel
    note: str = ""


def _classes(column: str, bounds: list[tuple[str, str, int, int | None]]) -> tuple[Measure, ...]:
    return tuple(Measure(i, label, f"{column} >= {low}" + (f" AND {column} <= {high}"
                                                           if high is not None else ""))
                 for i, label, low, high in bounds)


_AGE = "(:now - n.modified)"
ANALYSES: dict[str, Analysis] = {a.id: a for a in (
    Analysis("freitext", "Füllgrade der Freitextfelder", (
        Measure("titel", "Titel", "n.title_len > 0"),
        Measure("beschreibung", "Beschreibung", "n.desc_len > 0"),
        Measure("schlagwoerter", "Schlagwörter", "n.n_keywords > 0"),
        Measure("adresse", "Web-Adresse", "n.url_key IS NOT NULL"),
        Measure("autor", "Autor", "n.has_author = 1")), rate=True),
    Analysis("beschreibung", "Länge der Beschreibung", _classes("n.desc_len", [
        ("leer", "leer", 0, 0), ("kurz", "1 bis 49 Zeichen", 1, 49),
        ("mittel", "50 bis 199", 50, 199), ("lang", "200 bis 999", 200, 999),
        ("sehr_lang", "ab 1.000", 1000, None)])),
    Analysis("titel", "Länge des Titels", (*_classes("n.title_len", [
        ("leer", "leer", 0, 0), ("sehr_kurz", "1 bis 9 Zeichen", 1, 9),
        ("kurz", "10 bis 39", 10, 39), ("mittel", "40 bis 99", 40, 99),
        ("lang", "ab 100", 100, None)]),
        Measure("gleich", "Beschreibung wiederholt nur den Titel", "n.title_is_desc = 1"))),
    Analysis("schlagwoerter", "Anzahl der Schlagwörter", _classes("n.n_keywords", [
        ("keine", "keine", 0, 0), ("wenige", "1 bis 2", 1, 2), ("einige", "3 bis 5", 3, 5),
        ("viele", "6 bis 10", 6, 10), ("sehr_viele", "mehr als 10", 11, None)])),
    Analysis("duenn", "Dünne Metadaten", (
        Measure("ohne_beschreibung", "ohne Beschreibung", "n.desc_len = 0"),
        Measure("ohne_fach", "ohne Fach", "n.n_subjects = 0"),
        Measure("ohne_stufe", "ohne Bildungsstufe", "n.n_levels = 0"),
        Measure("duenn", "ohne alle drei (dünn)",
                "n.desc_len = 0 AND n.n_subjects = 0 AND n.n_levels = 0"),
        Measure("ohne_schlagwoerter", "ohne Schlagwörter", "n.n_keywords = 0"),
        Measure("ohne_materialart", "ohne Materialart", "n.n_types = 0")),
        note="„Dünn“ wie im Quellenpanel: weder Beschreibung noch Fach noch Bildungsstufe."),
    Analysis("vollstaendigkeit", "Vollständigkeit (0 bis 8 Kernangaben)", tuple(
        Measure(i, label, f"({_SCORE}) BETWEEN {low} AND {high}")
        for i, label, low, high in (("s0", "0 bis 2 Angaben", 0, 2), ("s3", "3 bis 4", 3, 4),
                                    ("s5", "5 bis 6", 5, 6), ("s7", "7 bis 8", 7, 8))),
        note="Gezählt werden Titel, Beschreibung, Schlagwörter, Fach, Bildungsstufe, "
             "Materialart, Lizenz und Sprache."),
    Analysis("dubletten", "Verdacht auf Dubletten", (
        Measure("adresse_mehrfach", "Web-Adresse mehrfach vergeben", _DUP_URL),
        Measure("adresse_fremd", "dieselbe Web-Adresse in einer anderen Quelle", _CROSS_URL),
        Measure("titel_mehrfach", "Titel mehrfach vergeben", _DUP_TITLE)),
        note="Verglichen werden Web-Adressen ohne http/https, „www.“ und Schrägstrich am "
             "Ende sowie Titel ohne Groß- und Kleinschreibung. Gleiche Titel sind nur ein "
             "Hinweis (Reihen, Folgen)."),
    Analysis("alter", "Letzte Änderung", (
        Measure("j1", "im letzten Jahr", f"{_AGE} < {YEAR}"),
        Measure("j3", "vor 1 bis 3 Jahren", f"{_AGE} >= {YEAR} AND {_AGE} < {3 * YEAR}"),
        Measure("j5", "vor 3 bis 5 Jahren", f"{_AGE} >= {3 * YEAR} AND {_AGE} < {5 * YEAR}"),
        Measure("alt", "vor mehr als 5 Jahren", f"{_AGE} >= {5 * YEAR}"),
        Measure("unbekannt", "unbekannt", "n.modified IS NULL"))),
    Analysis("adressen", "Web-Adressen", (
        Measure("https", "https", "n.url_https = 1"),
        Measure("http", "nur http", "n.url_https = 0"),
        Measure("keine", "keine Web-Adresse", "n.url_key IS NULL"))),
)}
# Auswertungen mit eigener Form (Zeilen sind keine Kennzahlen)
SPECIAL = {"jahrgang": "Materialien nach Jahr der Erfassung",
           "rechner": "Häufigste Rechner der Web-Adressen"}
NAMES = (*ANALYSES, *SPECIAL)


def _share(value: int | None, total: int | None, digits: int = 2) -> float | None:
    return None if value is None or not total else round(100 * value / total, digits)


def _stamp(state: dict[str, str]) -> float | None:
    try:
        return float(state["finished_at"])
    except (KeyError, ValueError):
        return None


def run_analysis(snapshot: Snapshot, spec: Any, instance: Any) -> dict[str, Any]:
    """``spec`` ist die geprüfte Abfrage (``QuerySpec`` mit ``snapshot``)."""
    ask = spec.snapshot
    state = snapshot.state()
    stamp = _stamp(state)
    if stamp is None:
        raise QueryError(
            "Für diese Instanz gibt es noch keinen vollständigen Abzug. Er lässt sich unter "
            "„Qualität“, Reiter „Tiefenprüfung“, starten.")
    notes = Notes()
    when = datetime.fromtimestamp(stamp, UTC).strftime("%d.%m.%Y %H:%M UTC")
    params = {"now": stamp, "kind": ask.by}

    def table(con: sqlite3.Connection) -> tuple[list[dict[str, Any]], list[dict[str, Any]], int]:
        total = con.execute("SELECT COUNT(*) FROM nodes").fetchone()[0]
        if ask.analysis == "jahrgang":
            columns, rows, total = _years(con, total)
            if ask.show == "count":
                columns = columns[:1]
                rows = [{**r, "values": r["values"][:1]} for r in rows]
            return columns, rows, total
        if ask.analysis == "rechner":
            return _hosts(con, total, ask.top)
        analysis = ANALYSES[ask.analysis]
        sums = ", ".join(f"SUM(CASE WHEN {m.where} THEN 1 ELSE 0 END)" for m in analysis.measures)
        if ask.by is None:
            got = con.execute(f"SELECT {sums} FROM nodes n", params).fetchone()  # noqa: S608
            columns = [
                {"id": "count", "label": "Anzahl", "derived": False, "total": None},
                {"id": "share", "label": "Anteil %" if analysis.rate else "Anteil (%)",
                 "derived": True, "total": None}]
            rows = [{"key": m.id, "label": m.label, "kind": "value",
                     "values": [got[n] or 0, _share(got[n] or 0, total)]}
                    for n, m in enumerate(analysis.measures)]
            if ask.show == "count":  # nur Zählungen, etwa für eine Grafik
                columns = columns[:1]
                rows = [{**r, "values": r["values"][:1]} for r in rows]
            return columns, rows, total
        found = con.execute(
            f"SELECT m.name, COUNT(*), {sums} FROM nodes n JOIN memberships m "  # noqa: S608
            "ON m.node = n.id AND m.kind = :kind GROUP BY m.name "
            "ORDER BY COUNT(*) DESC, m.name LIMIT :top", {**params, "top": ask.top}).fetchall()
        groups = con.execute("SELECT COUNT(DISTINCT name) FROM memberships WHERE kind = ?",
                             (ask.by,)).fetchone()[0]
        if groups > len(found):
            notes.add("rows_top", f"Angezeigt werden die ersten {len(found)} von {groups} "
                                  "Zeilen.")
        whole = con.execute(f"SELECT {sums} FROM nodes n", params).fetchone()  # noqa: S608
        share = ask.show == "share"
        suffix = (" %" if analysis.rate else " (%)") if share else ""
        columns = [{"id": "count", "label": "Anzahl", "derived": False, "total": total}]
        for n, m in enumerate(analysis.measures):
            value = whole[n] or 0
            columns.append({"id": m.id, "label": m.label + suffix, "derived": share,
                            "total": _share(value, total) if share else value})
        rows = [{"key": r[0], "label": r[0], "kind": "value",
                 "values": [r[1], *[(_share(r[2 + n] or 0, r[1]) if share else r[2 + n] or 0)
                                    for n in range(len(analysis.measures))]]}
                for r in found]
        return columns, rows, total

    columns, rows, total = snapshot.run(table)
    notes.add("snapshot",
              f"Aus dem lokalen Abzug vom {when}: {total} Materialien. Der Abzug enthält nur "
              "Merkmale (Längen, Anzahlen, Zeitpunkte), keine Inhalte.")
    if state.get("status") == "running":
        notes.add("snapshot_running", "Der Abzug wird gerade erneuert; die Zahlen können "
                                      "sich während des Laufs ändern.")
    analysis = ANALYSES.get(ask.analysis)
    if analysis is not None and analysis.note:
        notes.add("snapshot_method", analysis.note)
    if ask.by is not None:
        if ask.by == "publisher":
            notes.add("rows_overlap", "Ein Material mit mehreren Bezugsquellen steht in "
                                      "mehreren Zeilen.")
        if ask.show == "share":
            notes.add("snapshot_share", "Anteile beziehen sich auf die Materialien der Zeile.")
    result: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION, "instance": spec.instance,
        "columns": columns, "rows": rows,
        "provenance": {
            "fetched_at": stamp, "oldest_fetched_at": stamp, "cached": True, "queries": 0,
            "notes": list(notes),
            "instance": {"id": instance.id, "base_url": instance.base_url,
                         "repository": instance.repository,
                         "metadataset": instance.metadataset, "query": instance.query},
            "spec": spec.model_dump(mode="json", exclude={"refresh"}),
            "rows": None, "filters": [],
            "columns": [{"id": c["id"], "filters": [], "formula": None} for c in columns],
        },
    }
    if ask.by is None:
        result["total_row"] = False
        result["base_total"] = total
    return result


def _years(con: sqlite3.Connection, total: int):
    found = con.execute(
        "SELECT CAST(strftime('%Y', created, 'unixepoch') AS INTEGER) AS y, COUNT(*) "
        "FROM nodes WHERE created IS NOT NULL GROUP BY y ORDER BY y").fetchall()
    known = sum(r[1] for r in found)
    rows = [{"key": str(r[0]), "label": str(r[0]), "kind": "value",
             "values": [r[1], _share(r[1], total)]} for r in found]
    if total - known:
        rows.append({"key": "__missing__", "label": "unbekannt", "kind": "missing",
                     "values": [total - known, _share(total - known, total)]})
    return ([{"id": "count", "label": "Anzahl", "derived": False, "total": total},
             {"id": "share", "label": "Anteil (%)", "derived": True, "total": None}],
            rows, total)


def _hosts(con: sqlite3.Connection, total: int, top: int):
    found = con.execute(
        "SELECT url_host, COUNT(*), COUNT(DISTINCT url_key) FROM nodes "
        "WHERE url_host IS NOT NULL GROUP BY url_host ORDER BY COUNT(*) DESC, url_host "
        "LIMIT ?", (top,)).fetchall()
    rows = [{"key": r[0], "label": r[0], "kind": "value",
             "values": [r[1], _share(r[1], total), r[1] - r[2]]} for r in found]
    return ([{"id": "count", "label": "Anzahl", "derived": False, "total": total},
             {"id": "share", "label": "Anteil (%)", "derived": True, "total": None},
             {"id": "repeated", "label": "davon Wiederholungen derselben Adresse",
              "derived": False, "total": None}], rows, total)
