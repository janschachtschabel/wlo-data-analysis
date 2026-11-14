"""Was der Assistent tun kann: Felder und Werte nachschlagen, zählen, Ansichten vorschlagen.

Kein Werkzeug liefert Datensätze und keines schreibt. Zählungen laufen durch dieselbe Engine
wie alle Auswertungen, Vorschläge durch dieselbe Prüfung wie gespeicherte Definitionen.
"""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from typing import Any

from pydantic import ValidationError

from ..edusharing.errors import EduSharingError
from ..edusharing.vocab import UnresolvedValue
from ..engine.engine import run_query
from ..engine.spec import QuerySpec
from ..errors import QueryError, describe_validation
from ..report.build import build_report
from ..report.formats import report_data
from ..store.definitions import NotFound
from ..store.runner import DamagedRun
from ..store.specs import InvalidDefinition, parse_spec

MAX_ROWS, MAX_COLUMNS, MAX_RESULT_CHARS = 40, 12, 12000
MAX_RUN_TABLES, MAX_DASHBOARDS = 30, 2
MAX_TILES = 12
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
]
RUN_TOOL = _tool("read_run", "Die Tabellen der Auswertung, die der Nutzer gerade ansieht.",
                 {}, [])


def _norm(text: str) -> str:
    return text.casefold().strip()


def _dump(value: Any) -> str:
    """Immer gültiges JSON: Was zu groß ist, wird nicht mitten im Text abgeschnitten."""
    text = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    if len(text) > MAX_RESULT_CHARS:
        text = json.dumps({"fehler": "Das Ergebnis ist zu groß. Bitte enger fragen (weniger "
                                     "Zeilen oder Spalten, top setzen)."}, ensure_ascii=False)
    return text


def _size(value: Any) -> int:
    return len(json.dumps(value, ensure_ascii=False, separators=(",", ":")))


def compact(result: dict[str, Any]) -> dict[str, Any]:
    """Ein Ergebnis der Engine in der Kürze, die ein Modell braucht."""
    columns = result["columns"][:MAX_COLUMNS]
    shown = result["rows"][:MAX_ROWS]
    out: dict[str, Any] = {
        "spalten": ["Zeile", *[c["label"] for c in columns]],
        "zeilen": [[r["label"], *r["values"][:MAX_COLUMNS]] for r in shown],
    }
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
        if name == "read_run":
            return "Liest die Tabellen der Auswertung"
        return "Unbekannter Schritt"

    async def call(self, name: str, args: Any) -> str:
        """Führt ein Werkzeug aus. Fehler kommen als Text zurück, damit das Modell es
        anders versuchen kann; nichts hier wirft wegen einer Eingabe des Modells."""
        handlers: dict[str, Callable[[dict[str, Any]], Awaitable[Any]]] = {
            "list_fields": self._list_fields, "field_values": self._field_values,
            "run_query": self._run_query, "propose_view": self._propose_view,
            "propose_dashboard": self._propose_dashboard}
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
            try:
                await self._vocab.check_facet(flt.property)
            except EduSharingError:
                raise QueryError(
                    f"Auf '{flt.property[:80]}' kann hier nicht gefiltert werden. Filter gehen "
                    "auf Feldern mit Werteliste, auf Quelle und Bezugsquelle und auf "
                    "'license'.") from None
        return spec

    async def _run_query(self, args: dict[str, Any]) -> Any:
        return compact(await run_query(await self._query(args.get("spec")), self._client,
                                       self._vocab))

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
            raise QueryError(f"Je Frage höchstens {MAX_DASHBOARDS} Dashboard-Vorschläge.")
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
        await self._preview("dashboard", name, spec)
        description = " ".join(str(args.get("description") or "").split())[:1000]
        self.on_proposal({"kind": "dashboard", "name": name, "description": description,
                           "spec": spec, "report": None})
        return {"gezeigt": name, "kacheln": len(tiles)}

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

