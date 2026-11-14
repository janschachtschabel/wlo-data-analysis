"""Was der Assistent tun kann: Felder und Werte nachschlagen, zählen, Ansichten vorschlagen.

Kein Werkzeug liefert Datensätze und keines schreibt. Zählungen laufen durch dieselbe Engine
wie alle Auswertungen, Vorschläge durch dieselbe Prüfung wie gespeicherte Definitionen.
"""

from __future__ import annotations

import json
import re
import unicodedata
from collections.abc import Awaitable, Callable
from typing import Any

from pydantic import ValidationError

from ..edusharing.errors import EduSharingError
from ..edusharing.vocab import UnresolvedValue
from ..engine.engine import run_query
from ..engine.spec import QuerySpec
from ..errors import QueryError, describe_validation
from ..filters import TIME_CRITERIA, SinceValue
from ..report.build import build_report
from ..report.formats import report_data
from ..store.definitions import NotFound
from ..store.runner import DamagedRun
from ..store.specs import InvalidDefinition, parse_spec

MAX_ROWS, MAX_COLUMNS, MAX_RESULT_CHARS = 40, 12, 12000
MAX_RUN_TABLES, MAX_DASHBOARDS = 30, 2
MAX_TILES = 12
MAX_SECTIONS, MAX_GROUPS, MAX_BLOCKS = 12, 40, 6
# Eine gegliederte Tabelle kostet je Block und Spalte Abfragen: wenige davon je Vorschlag
MAX_MATRICES, MAX_MATRIX_COLUMNS, MAX_GROUP_VALUES = 3, 12, 300
RESERVED = {"round", "max", "min"}  # Wörter der Formeln taugen nicht als Kennung
VIEWS = ("table", "number", "bar", "stacked_bar", "line", "pie", "heatmap")

_SPEC = {"type": "object", "description": "Abfrage im beschriebenen Format (ohne instance)."}
_VIEW = {"type": "string", "enum": list(VIEWS), "description": "Darstellung"}


def _tool(name: str, description: str, properties: dict[str, Any], required: list[str]
          ) -> dict[str, Any]:
    return {"type": "function", "function": {
        "name": name, "description": description,
        "parameters": {"type": "object", "properties": properties, "required": required}}}


TOOLS = [
    _tool("list_fields", "Felder der Instanz mit Bezeichnung. Mit search nach Name oder "
          "Bezeichnung suchen.", {"search": {"type": "string"}}, []),
    _tool("field_values", "Werte der Werteliste eines Feldes (URI und Bezeichnung). Mit "
          "search eingrenzen.", {"property": {"type": "string"},
                                 "search": {"type": "string"}}, ["property"]),
    _tool("run_query", "Zählt. Liefert eine Tabelle aus Zählungen mit Hinweisen.",
          {"spec": _SPEC}, ["spec"]),
    _tool("propose_view", "Zeigt dem Nutzer eine Ansicht (Tabelle oder Grafik) als Karte, "
          "die er in den Explorer übernehmen oder als Kennzahl speichern kann.",
          {"title": {"type": "string"}, "spec": _SPEC, "view": _VIEW}, ["title", "spec"]),
    _tool("propose_dashboard", "Schlägt ein Dashboard aus mehreren Kacheln vor, das der "
          "Nutzer mit einem Klick anlegen kann.",
          {"name": {"type": "string"}, "description": {"type": "string"},
           "tiles": {"type": "array", "maxItems": MAX_TILES, "items": {
               "type": "object", "properties": {
                   "title": {"type": "string"}, "spec": _SPEC, "view": _VIEW,
                   "width": {"type": "string", "enum": ["half", "full"]}},
               "required": ["title", "spec"]}}}, ["name", "tiles"]),
    _tool("propose_template", "Schlägt einen Bericht (Template) aus Abschnitten vor, den der "
          "Nutzer mit einem Klick anlegen kann: Text, Tabelle, Grafik und gegliederte Tabelle "
          "(Zeilengruppen × Spalten × Blöcke).",
          {"name": {"type": "string"}, "description": {"type": "string"},
           "sections": {"type": "array", "maxItems": MAX_SECTIONS, "items": {
               "type": "object", "properties": {
                   "type": {"type": "string", "enum": ["text", "table", "chart", "matrix"]},
                   "markdown": {"type": "string", "description": "nur text"},
                   "title": {"type": "string"},
                   "spec": {**_SPEC, "description": "nur table und chart: Abfrage"},
                   "view": _VIEW,
                   "rows": {"type": "object", "description": "nur matrix: {property, groups: "
                            "[{label, values: [Bezeichnung oder URI]}], missing}"},
                   "columns": {"type": "array", "description": "nur matrix: [{id, label, "
                               "filters oder formula}]", "items": {"type": "object"}},
                   "blocks": {"type": "array", "description": "nur matrix: [{label, filters}]",
                              "items": {"type": "object"}}},
               "required": ["type"]}}}, ["name", "sections"]),
]
RUN_TOOL = _tool("read_run", "Die Tabellen der Auswertung, die der Nutzer gerade ansieht.",
                 {}, [])


_UMLAUTS = str.maketrans({"ä": "ae", "ö": "oe", "ü": "ue", "ß": "ss"})


def ident(label: str, used: set[str]) -> str:
    """Kennung aus einem Namen (klein, a–z, Ziffern, Unterstrich), die es noch nicht gibt."""
    plain = unicodedata.normalize("NFKD", label.lower().translate(_UMLAUTS))
    base = re.sub(r"[^a-z0-9]+", "_", "".join(
        c for c in plain if not unicodedata.combining(c))).strip("_") or "s"
    if base[0].isdigit():
        base = "s_" + base
    base = base[:40].rstrip("_")
    candidate, n = base, 1
    while candidate in used or candidate in RESERVED:
        n += 1
        candidate = base[:40 - len(str(n)) - 1] + f"_{n}"
    used.add(candidate)
    return candidate


def _text(value: Any, limit: int = 200) -> str:
    return " ".join(str(value or "").split())[:limit]


def _norm(text: str) -> str:
    return text.casefold().strip()


def _dump(value: Any) -> str:
    """Immer gültiges JSON: Was zu groß ist, wird nicht mitten im Text abgeschnitten."""
    text = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    if len(text) > MAX_RESULT_CHARS:
        text = json.dumps({"fehler": "Das Ergebnis ist zu groß. Bitte enger fragen (weniger "
                                     "Zeilen oder Spalten, top setzen)."}, ensure_ascii=False)
    return text


MAX_PREVIEW_CHARS = 400_000  # Vorschau eines Dashboards im Chat


def _size(value: Any) -> int:
    return len(json.dumps(value, ensure_ascii=False, separators=(",", ":")))


def _themes(rows: list[dict[str, Any]]) -> list[list[Any]] | None:
    """Themen je Zelle in Kurzform: je Zeile und Spalte die ersten fünf mit Anzahl."""
    if not any(r.get("topics") for r in rows):
        return None
    return [[r["label"], *[", ".join(f"{str(t.get('label'))[:60]} ({t.get('count')})"
                                     for t in cell[:5])
                           for cell in (r.get("topics") or [])[:MAX_COLUMNS]]]
            for r in rows]


def compact(result: dict[str, Any]) -> dict[str, Any]:
    """Ein Ergebnis der Engine in der Kürze, die ein Modell braucht."""
    columns = result["columns"][:MAX_COLUMNS]
    shown = result["rows"][:MAX_ROWS]
    out: dict[str, Any] = {
        "spalten": ["Zeile", *[c["label"] for c in columns]],
        "zeilen": [[r["label"], *r["values"][:MAX_COLUMNS]] for r in shown],
    }
    if (themes := _themes(shown)) is not None:
        out["themen"] = themes
    if any(r.get("percent") for r in shown):
        out["anteile"] = [[r["label"], *(r.get("percent") or [])[:MAX_COLUMNS]] for r in shown]
    if result.get("total_row", True):
        out["gesamt"] = ["Gesamt", *[c.get("total") for c in columns]]
    if result.get("base_total") is not None:
        out["grundgesamtheit"] = result["base_total"]
    if len(result["rows"]) > MAX_ROWS:
        out["weitere_zeilen"] = len(result["rows"]) - MAX_ROWS
    if len(result["columns"]) > MAX_COLUMNS:
        out["weitere_spalten"] = len(result["columns"]) - MAX_COLUMNS
    notes = [n["text"] for n in result.get("provenance", {}).get("notes", [])][:6]
    if notes:
        out["hinweise"] = notes
    return out


def _compact_table(block: dict[str, Any]) -> dict[str, Any]:
    columns = block.get("columns", [])[:MAX_COLUMNS]
    every = block.get("rows", [])
    rows = [r for r in every if r.get("kind") != "total"]
    total = next((r for r in every if r.get("kind") == "total"), None)
    return {"titel": block.get("title"), "spalten": ["Zeile", *[c["label"] for c in columns]],
            "zeilen": [[r["label"], *r["values"][:MAX_COLUMNS]] for r in rows[:MAX_ROWS]],
            **({"gesamt": ["Gesamt", *total["values"][:MAX_COLUMNS]]} if total else {}),
            **({"themen": themes} if (themes := _themes(rows[:MAX_ROWS])) else {}),
            **({"weitere_spalten": len(block["columns"]) - MAX_COLUMNS}
               if len(block.get("columns", [])) > MAX_COLUMNS else {}),
            **({"weitere_zeilen": len(rows) - MAX_ROWS} if len(rows) > MAX_ROWS else {}),
            **({"hinweise": [n["text"] for n in block["notes"][:4]]}
               if block.get("notes") else {})}


class Toolbox:
    """Die Werkzeuge einer Unterhaltung, gebunden an eine Instanz (und einen Lauf)."""

    def __init__(self, services: Any, instance: str, run_id: str | None = None,
                 on_proposal: Callable[[dict[str, Any]], None] | None = None,
                 definition_id: str | None = None) -> None:
        self._services, self.instance, self.run_id = services, instance, run_id
        self.definition_id = definition_id
        self._client, self._vocab = services.client(instance), services.vocab(instance)
        self.on_proposal = on_proposal or (lambda proposal: None)
        self._dashboards = 0

    @property
    def definitions(self) -> list[dict[str, Any]]:
        return TOOLS + ([RUN_TOOL] if self.has_context else [])

    @property
    def has_context(self) -> bool:
        """Die Unterhaltung hängt an einer Auswertung (gespeicherter Lauf oder Definition)."""
        return bool(self.run_id or self.definition_id)

    def describe(self, name: str, args: dict[str, Any]) -> str:
        """Ein Satz für die Oberfläche, was gerade geschieht."""
        if name == "list_fields":
            return "Sieht die Felder durch" + (f" („{str(args.get('search'))[:60]}“)"
                                               if args.get("search") else "")
        if name == "field_values":
            return f"Schlägt Werte nach: {str(args.get('property'))[:80]}"
        if name == "run_query":
            return "Zählt" + _about(args.get("spec"))
        if name == "propose_view":
            return f"Bereitet eine Ansicht vor: {str(args.get('title'))[:80]}"
        if name == "propose_dashboard":
            return f"Stellt ein Dashboard zusammen: {str(args.get('name'))[:80]}"
        if name == "propose_template":
            return f"Stellt einen Bericht zusammen: {str(args.get('name'))[:80]}"
        if name == "read_run":
            return "Liest die Tabellen der Auswertung"
        return "Unbekannter Schritt"

    async def call(self, name: str, args: Any) -> str:
        """Führt ein Werkzeug aus. Fehler kommen als Text zurück, damit das Modell es
        anders versuchen kann; nichts hier wirft wegen einer Eingabe des Modells."""
        handlers: dict[str, Callable[[dict[str, Any]], Awaitable[Any]]] = {
            "list_fields": self._list_fields, "field_values": self._field_values,
            "run_query": self._run_query, "propose_view": self._propose_view,
            "propose_dashboard": self._propose_dashboard,
            "propose_template": self._propose_template}
        if self.has_context:
            handlers["read_run"] = self._read_run
        handler = handlers.get(name)
        if handler is None:
            return _dump({"fehler": f"Das Werkzeug '{str(name)[:40]}' gibt es nicht."})
        if not isinstance(args, dict):
            return _dump({"fehler": "Die Argumente müssen ein JSON-Objekt sein."})
        try:
            return _dump(await handler(args))
        except (QueryError, InvalidDefinition) as exc:
            return _dump({"fehler": str(exc)})
        except UnresolvedValue as exc:
            return _dump({"fehler": str(exc), "vorschlaege": exc.suggestions[:8]})
        except EduSharingError as exc:
            return _dump({"fehler": exc.message})
        except (NotFound, DamagedRun) as exc:
            return _dump({"fehler": str(exc)})

    async def _list_fields(self, args: dict[str, Any]) -> Any:
        needle = _norm(str(args.get("search") or ""))
        fields = [f for f in (await self._vocab.fields()).values()
                  if not f.property.startswith("virtual:")]
        if needle:
            fields = [f for f in fields
                      if needle in _norm(f.caption or "") or needle in f.property.casefold()]
        fields.sort(key=lambda f: (not f.values, (f.caption or f.property).casefold()))
        return {"felder": [{"property": f.property, "bezeichnung": f.caption, "art": f.type,
                            "werte": len(f.values)} for f in fields[:80]],
                **({"weitere": len(fields) - 80} if len(fields) > 80 else {})}

    async def _field_values(self, args: dict[str, Any]) -> Any:
        prop = str(args.get("property") or "")
        field = await self._vocab.field(prop)
        if field is None:
            return {"fehler": f"Das Feld '{prop[:80]}' gibt es nicht. list_fields zeigt die "
                              "Felder."}
        if not field.values:
            return {"fehler": "Das Feld hat keine Werteliste. Vorkommende Werte zeigt "
                              "run_query mit rows auf diesem Feld, sofern es zählbar ist."}
        needle = _norm(str(args.get("search") or ""))
        values = [v for v in field.values
                  if not needle or needle in _norm(v.caption) or needle in v.id.casefold()]
        return {"feld": field.caption, "anzahl": len(values),
                "werte": [{"id": v.id, "bezeichnung": v.caption} for v in values[:60]]}

    async def _query(self, raw: Any) -> QuerySpec:
        if not isinstance(raw, dict):
            raise QueryError("spec muss ein JSON-Objekt sein.")
        try:
            spec = QuerySpec.model_validate({**raw, "instance": self.instance,
                                             "refresh": False})
        except ValidationError as exc:
            raise QueryError(describe_validation(exc.errors())) from None
        # Ein Filter auf Volltext, Titel oder Knoten-ID wäre eine Ja/Nein-Auskunft über
        # einzelne Datensätze: Das Modell filtert nur, wo es auch zählen darf.
        allowed = {"license", *self._client.instance.prefix_fields}
        for flt in [*spec.filters, *(f for s in spec.series for f in s.filters)]:
            if flt.property in allowed:
                continue
            if flt.property in TIME_CRITERIA and all(
                    isinstance(v, SinceValue) for v in flt.values):
                continue  # „neu seit“ und „geändert seit“: ein Zeitraum, kein Datensatz
            try:
                await self._vocab.check_facet(flt.property)
            except EduSharingError:
                raise QueryError(
                    f"Auf '{flt.property[:80]}' kann hier nicht gefiltert werden. Filter gehen "
                    "auf Feldern mit Werteliste, auf Quelle und Bezugsquelle und auf "
                    "'license'.") from None
        return spec

    async def _run_query(self, args: dict[str, Any]) -> Any:
        spec = await self._query(args.get("spec"))
        return compact(await run_query(
            spec, self._client, self._vocab,
            self._services.snapshot(self.instance) if spec.snapshot else None))

    async def _preview(self, kind: str, name: str, spec: dict[str, Any]) -> dict[str, Any]:
        run = await self._services.runner.preview(kind, name, spec, instance=self.instance)
        failed = [f"{r['title']}: {r['error'].get('detail', 'Fehler')}"
                  for r in run["results"] if r.get("error")]
        if failed:
            raise QueryError("Nicht vorgeschlagen, weil Abfragen scheitern. "
                             + " | ".join(failed)[:1500])
        return run

    async def _propose_view(self, args: dict[str, Any]) -> Any:
        title = " ".join(str(args.get("title") or "").split())[:200] or "Ansicht"
        view = args.get("view") if args.get("view") in VIEWS else "table"
        query = (await self._query(args.get("spec"))).model_dump(mode="json",
                                                                 exclude={"refresh"})
        spec = {"query": query, "view": {"type": view}}
        parse_spec("metric", spec)
        run = await self._preview("metric", title, spec)
        report = report_data(build_report(run), with_html=False)
        self.on_proposal({"kind": "metric", "name": title, "description": "", "spec": spec,
                           "report": report})
        tables = [b for b in report["blocks"] if b.get("type") == "table"]
        return {"gezeigt": title, "ergebnis": _compact_table(tables[0]) if tables else None}

    async def _propose_dashboard(self, args: dict[str, Any]) -> Any:
        name = " ".join(str(args.get("name") or "").split())[:200] or "Dashboard"
        self._dashboards += 1
        if self._dashboards > MAX_DASHBOARDS:  # jedes kostet viele Abfragen am Repositorium
            raise QueryError(f"Je Frage höchstens {MAX_DASHBOARDS} Vorschläge für Dashboards "
                             "oder Templates.")
        raw = args.get("tiles")
        if not isinstance(raw, list) or not raw:
            raise QueryError("tiles muss eine Liste mit mindestens einer Kachel sein.")
        if len(raw) > MAX_TILES:
            raise QueryError(f"Höchstens {MAX_TILES} Kacheln.")
        tiles, x, y = [], 0, 0
        for n, item in enumerate(raw, 1):
            if not isinstance(item, dict):
                raise QueryError(f"Kachel {n} muss ein JSON-Objekt sein.")
            try:
                query = await self._query(item.get("spec"))
            except QueryError as exc:
                raise QueryError(f"Kachel {n}: {exc}") from None
            width = 6 if item.get("width") == "half" else 12
            if x + width > 12:
                x, y = 0, y + 8
            tiles.append({
                "id": f"k{n}", "title": " ".join(str(item.get("title") or "").split())[:200]
                or f"Kachel {n}",
                "query": query.model_dump(mode="json", exclude={"refresh"}),
                "view": {"type": item.get("view") if item.get("view") in VIEWS else "table"},
                "layout": {"x": x, "y": y, "w": width, "h": 8}})
            x += width
            if x >= 12:
                x, y = 0, y + 8
        spec = {"instance": self.instance, "tiles": tiles}
        parse_spec("dashboard", spec)
        run = await self._preview("dashboard", name, spec)
        description = " ".join(str(args.get("description") or "").split())[:1000]
        # die Vorschau ist schon gerechnet: Der Nutzer sieht die Kacheln mit ihren Grafiken
        report: dict[str, Any] | None = report_data(build_report(run), with_html=False)
        if _size(report) > MAX_PREVIEW_CHARS:  # dann zeigt die Karte nur die Titel
            report = None
        self.on_proposal({"kind": "dashboard", "name": name, "description": description,
                           "spec": spec, "report": report})
        if report is None:
            return {"gezeigt": name, "kacheln": len(tiles),
                    "hinweis": "Die Vorschau ist zu groß für den Chat; der Nutzer sieht nur "
                               "die Titel der Kacheln. Sag ihm das und biete an, die Zeilen "
                               "mit top zu begrenzen."}
        return {"gezeigt": name, "kacheln": len(tiles),
                "darstellungen": [t["view"]["type"] for t in tiles],
                "hinweis": "Der Nutzer sieht die Vorschau mit allen Kacheln. Angelegt ist das "
                           "Dashboard erst, wenn er selbst auf „Dashboard anlegen“ klickt."}

    async def _propose_template(self, args: dict[str, Any]) -> Any:
        name = _text(args.get("name")) or "Bericht"
        self._dashboards += 1
        if self._dashboards > MAX_DASHBOARDS:  # jeder kostet viele Abfragen am Repositorium
            raise QueryError(f"Je Frage höchstens {MAX_DASHBOARDS} Vorschläge für Dashboards "
                             "oder Templates.")
        raw = args.get("sections")
        if not isinstance(raw, list) or not raw:
            raise QueryError("sections muss eine Liste mit mindestens einem Abschnitt sein.")
        if len(raw) > MAX_SECTIONS:
            raise QueryError(f"Höchstens {MAX_SECTIONS} Abschnitte.")
        if sum(1 for item in raw if isinstance(item, dict)
               and item.get("type") == "matrix") > MAX_MATRICES:
            raise QueryError(f"Höchstens {MAX_MATRICES} gegliederte Tabellen je Bericht.")
        sections = []
        for n, item in enumerate(raw, 1):
            try:
                section = await self._section(f"a{n}", item)
                # dieselbe Prüfung wie beim Speichern, je Abschnitt, damit der Fehler ihn nennt
                parse_spec("template", {"title": name, "instance": self.instance,
                                        "sections": [section]})
            except (QueryError, InvalidDefinition) as exc:
                raise QueryError(f"Abschnitt {n}: {exc}") from None
            sections.append(section)
        spec = {"instance": self.instance, "title": name, "sections": sections}
        parse_spec("template", spec)
        run = await self._preview("template", name, spec)
        # Texte kommen als fertiges, maskiertes HTML mit, wie in jeder Vorschau der App
        report: dict[str, Any] | None = report_data(build_report(run), with_html=True)
        tables = [b for b in (report or {}).get("blocks", []) if b.get("type") == "table"]
        if _size(report) > MAX_PREVIEW_CHARS:  # dann zeigt die Karte nur die Abschnitte
            report = None
        built = report or {}
        self.on_proposal({"kind": "template", "name": name,
                          "description": _text(args.get("description"), 1000), "spec": spec,
                          "report": report})
        open_ones = [n["text"] for n in built.get("notes", [])
                     if n.get("code") == "placeholder_unknown"][:10]
        return {"gezeigt": name, "abschnitte": len(sections),
                **({"offene_platzhalter": open_ones} if open_ones else {}),
                "platzhalter": {
                    "allgemein": ["{{run.date}}", "{{run.instance}}"],
                    **{t["key"]: [f"{{{{{t['key']}.total.{c['id']}}}}}"
                                  for c in t["columns"][:8]] for t in tables[:8]}},
                "hinweis": ("Der Nutzer sieht die Vorschau des Berichts." if report else
                            "Die Vorschau ist zu groß für den Chat; der Nutzer sieht nur die "
                            "Abschnitte.")
                + " Angelegt ist das Template erst, wenn er selbst auf „Template anlegen“ "
                  "klickt. Handeinträge und OERSI-Blöcke ergänzt er danach im Baukasten."}

    async def _section(self, sid: str, item: Any) -> dict[str, Any]:
        if not isinstance(item, dict):
            raise QueryError("muss ein JSON-Objekt sein.")
        kind = item.get("type")
        if kind == "text":
            markdown = item.get("markdown")
            if not isinstance(markdown, str) or not markdown.strip():
                raise QueryError("Ein Text-Abschnitt braucht markdown.")
            return {"id": sid, "type": "text", "markdown": markdown[:20000]}
        title = _text(item.get("title")) or "Tabelle"
        if kind in ("table", "chart"):
            query = await self._query(item.get("spec"))
            default = "bar" if kind == "chart" else "table"
            view = item.get("view") if item.get("view") in VIEWS else default
            return {"id": sid, "type": kind, "title": title, "view": {"type": view},
                    "query": query.model_dump(mode="json", exclude={"refresh"})}
        if kind == "matrix":
            return await self._matrix(sid, title, item)
        raise QueryError("type muss text, table, chart oder matrix sein.")

    async def _matrix(self, sid: str, title: str, item: dict[str, Any]) -> dict[str, Any]:
        rows = item.get("rows")
        if not isinstance(rows, dict) or not isinstance(rows.get("property"), str):
            raise QueryError("Eine gegliederte Tabelle braucht rows mit property und groups.")
        prop = rows["property"]
        try:
            await self._vocab.check_facet(prop)
        except EduSharingError:
            raise QueryError(f"Nach '{prop[:80]}' lässt sich nicht aufschlüsseln.") from None
        groups = rows.get("groups")
        if "missing" in rows and not isinstance(rows["missing"], bool):
            raise QueryError("rows.missing muss true oder false sein.")
        if not isinstance(groups, list) or not groups or len(groups) > MAX_GROUPS:
            raise QueryError(f"rows.groups muss eine Liste mit 1 bis {MAX_GROUPS} Gruppen sein.")
        used: set[str] = set()
        seen: dict[str, str] = {}
        sections = []
        for group in groups:
            label = _text(group.get("label") if isinstance(group, dict) else "")
            values = group.get("values") if isinstance(group, dict) else None
            if not label or not isinstance(values, list) or not values:
                raise QueryError(f"Die Gruppe „{label or '?'}“ braucht label und mindestens "
                                 "einen Wert in values.")
            if len(values) > MAX_GROUP_VALUES:
                raise QueryError(f"Die Gruppe „{label}“ hat mehr als {MAX_GROUP_VALUES} Werte.")
            if label in seen.values() or any(s["label"] == label for s in sections):
                raise QueryError(f"Die Gruppe „{label}“ steht zweimal in rows.groups.")
            resolved = []
            for value in values:
                found = (await self._vocab.resolve(prop, str(value))).value
                if found in seen:
                    if seen[found] != label:
                        raise QueryError(f"Der Wert '{str(value)[:80]}' steht in zwei Gruppen "
                                         f"(„{seen[found]}“ und „{label}“).")
                    continue
                seen[found] = label
                resolved.append({"value": found, "literal": True})
            sections.append({"id": ident(label, used), "label": label, "values": resolved})
        columns = item.get("columns")
        if not isinstance(columns, list) or not 1 <= len(columns) <= MAX_MATRIX_COLUMNS:
            raise QueryError(f"columns muss eine Liste mit 1 bis {MAX_MATRIX_COLUMNS} Spalten "
                             "sein.")
        ids: set[str] = {str(c.get("id")) for c in columns
                         if isinstance(c, dict) and c.get("id")}
        series = []
        for column in columns:
            if not isinstance(column, dict) or not _text(column.get("label")):
                raise QueryError("Jede Spalte braucht ein label.")
            entry: dict[str, Any] = {
                "id": str(column["id"]) if column.get("id") else ident(
                    _text(column["label"]), ids),
                "label": _text(column["label"])}
            if column.get("formula") and column.get("filters"):
                raise QueryError(f"Die Spalte „{entry['label']}“ braucht entweder filters "
                                 "oder formula, nicht beides.")
            if column.get("formula"):
                entry["formula"] = str(column["formula"])
            elif column.get("filters"):
                entry["filters"] = column["filters"]
            series.append(entry)
        raw_blocks = item.get("blocks") or [{"label": "Bestand"}]
        if not isinstance(raw_blocks, list) or len(raw_blocks) > MAX_BLOCKS:
            raise QueryError(f"blocks muss eine Liste mit höchstens {MAX_BLOCKS} Blöcken sein.")
        used_blocks: set[str] = set()
        blocks = []
        for block in raw_blocks:
            if not isinstance(block, dict) or not _text(block.get("label")):
                raise QueryError("Jeder Block braucht ein label.")
            if block.get("source") not in (None, "query"):
                raise QueryError("Blöcke gehen hier nur als Abfrage. Handeintrag und OERSI "
                                 "ergänzt der Nutzer im Baukasten; nenne ihm das im Text.")
            # dieselben Grenzen wie bei jeder Abfrage des Assistenten: Filter nur, wo er
            # zählen darf
            checked = await self._query({"filters": block.get("filters") or [],
                                         "series": series})
            blocks.append({"id": ident(_text(block["label"]), used_blocks),
                           "label": _text(block["label"]), "source": "query",
                           "filters": [f.model_dump(mode="json") for f in checked.filters]})
        return {"id": sid, "type": "matrix", "title": title,
                "rows": {"property": prop, "sections": sections,
                         "missing": rows.get("missing") is not False},
                "series": series, "blocks": blocks}

    async def _read_run(self, args: dict[str, Any]) -> Any:
        if self.run_id:
            run = await self._services.runner.get_run(self.run_id)
        else:  # die Definition frisch zählen (aus dem Zwischenspeicher, wenn eben gezeigt)
            # wie die Ansicht: gegen die Instanz der Definition, nicht die der Kopfzeile
            run = await self._services.runner.run(self.definition_id, instance=None,
                                                  save=False)
        report = report_data(build_report(run), with_html=False)
        tables = [b for b in report["blocks"] if b.get("type") == "table"]
        shown = tables[:MAX_RUN_TABLES]
        budget = (MAX_RESULT_CHARS - 1500) // max(1, len(shown))
        out = []
        for block in shown:
            table = _compact_table(block)
            if _size(table) > budget:
                table.pop("hinweise", None)
            while _size(table) > budget and table["zeilen"]:
                cut = len(table["zeilen"]) // 2
                table["weitere_zeilen"] = table.get("weitere_zeilen", 0) + len(
                    table["zeilen"]) - cut
                table["zeilen"] = table["zeilen"][:cut]
            if _size(table) > budget:  # selbst der Kopf ist zu breit: nur der Titel
                table = {"titel": str(table.get("titel"))[:120],
                         "weitere_zeilen": table.get("weitere_zeilen", 0)}
            out.append(table)
        failed = [str(b.get("title"))[:120] for b in report["blocks"]
                  if b.get("type") == "failure"][:20]
        instances = sorted({str(s.get("instance")) for b in tables
                            for s in b.get("sources") or [] if s.get("instance")})
        return {"auswertung": report.get("title"), "stand": report.get("finished_at"),
                **({"instanz": ", ".join(instances)[:200]} if instances else {}),
                "tabellen": out,
                **({"weitere_tabellen": len(tables) - len(shown)}
                   if len(tables) > len(shown) else {}),
                **({"fehlgeschlagen": failed} if failed else {})}


def _about(spec: Any) -> str:
    if not isinstance(spec, dict):
        return ""
    parts = []
    rows, cols = spec.get("rows"), spec.get("columns")
    if isinstance(spec.get("fill"), dict):
        parts.append("Füllgrade")
    if isinstance(spec.get("vocab_check"), dict):
        parts.append("Werte außerhalb der Wertelisten")
    if isinstance(spec.get("profile"), dict):
        parts.append(f"Merkmal {str(spec['profile'].get('property'))[:60]} je Zeile")
    if isinstance(spec.get("gaps"), dict):
        parts.append("Lückenliste")
    if isinstance(spec.get("snapshot"), dict):
        parts.append(f"aus dem lokalen Abzug ({str(spec['snapshot'].get('analysis'))[:40]})")
    if isinstance(spec.get("sizes"), dict):
        parts.append(f"Größenklassen von {str(spec['sizes'].get('property'))[:60]}")
    if isinstance(rows, dict) and rows.get("property"):
        parts.append(f"nach {str(rows['property'])[:60]}")
    if isinstance(cols, dict) and cols.get("property"):
        parts.append(f"× {str(cols['property'])[:60]}")
    filters = spec.get("filters")
    if isinstance(filters, list) and filters:
        parts.append(f"mit {len(filters)} Filter{'n' if len(filters) > 1 else ''}")
    return ": " + " ".join(parts) if parts else " die Gesamtzahl"

