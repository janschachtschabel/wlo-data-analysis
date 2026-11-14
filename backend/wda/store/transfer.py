"""Definitionen als JSON aus- und einführen. Ein Import gelingt ganz oder gar nicht."""

from __future__ import annotations

import copy
import sqlite3
from collections.abc import Callable
from typing import Any

from .db import Database
from .definitions import (
    ID_PATTERN,
    NotFound,
    clean_name,
    fetch,
    insert,
    new_id,
    now_iso,
    replace,
)
from .specs import KIND_LABELS, KINDS, InvalidDefinition, parse_spec

FORMAT, VERSION = "wda-definitions", 1
MAX_ENTRIES = 2000
MODES = ("skip", "replace", "copy")
_ORDER = {"filter": 0, "metric": 1, "dashboard": 2, "template": 2}  # Verwiesene zuerst


def _rewrite(spec: dict[str, Any], mapping: dict[str, str]) -> dict[str, Any]:
    """Verweise auf mitkopierte Definitionen auf deren neue IDs umschreiben."""
    spec = copy.deepcopy(spec)
    if isinstance(spec.get("filter_ids"), list):
        spec["filter_ids"] = [mapping.get(i, i) if isinstance(i, str) else i
                              for i in spec["filter_ids"]]
    for key in ("tiles", "sections"):
        for item in spec.get(key) or []:
            if isinstance(item, dict) and isinstance(item.get("metric_id"), str):
                item["metric_id"] = mapping.get(item["metric_id"], item["metric_id"])
    return spec


class Transfer:
    def __init__(self, db: Database, clock: Callable[[], str] = now_iso) -> None:
        self._db = db
        self._clock = clock

    async def export(self, ids: list[str] | None = None) -> dict[str, Any]:
        def fn(con: sqlite3.Connection) -> dict[str, Any]:
            if ids is None:
                wanted = [r["id"] for r in con.execute("SELECT id FROM definitions")]
            else:
                wanted, todo = [], list(dict.fromkeys(ids))
                while todo:  # Verwiesene Definitionen gehören dazu
                    current = todo.pop()
                    if current in wanted:
                        continue
                    fetch(con, current)
                    wanted.append(current)
                    todo += [r["target"] for r in con.execute(
                        "SELECT target FROM refs WHERE source=?", (current,))]
            definitions = sorted((fetch(con, i) for i in wanted),
                                 key=lambda d: (_ORDER[d.kind], d.id))
            group_ids = {d.group_id for d in definitions if d.group_id}
            groups = [{"id": r["id"], "name": r["name"]} for r in con.execute(
                "SELECT id, name FROM groups ORDER BY position, name")
                if ids is None or r["id"] in group_ids]
            return {
                "format": FORMAT, "version": VERSION, "exported_at": self._clock(),
                "groups": groups,
                "definitions": [
                    {"id": d.id, "kind": d.kind, "name": d.name, "description": d.description,
                     "group_id": d.group_id, "spec": d.spec} for d in definitions],
            }

        return await self._db.read(fn)

    async def import_(self, data: Any, mode: str = "skip") -> dict[str, Any]:
        if mode not in MODES:
            raise InvalidDefinition(f"Unbekannter mode '{mode}'. Erlaubt: {', '.join(MODES)}.")
        groups, entries = self._check_file(data)

        def fn(con: sqlite3.Connection) -> dict[str, Any]:
            now = self._clock()
            known_groups = {r["id"] for r in con.execute("SELECT id FROM groups")}
            group_report = []
            for group in groups:
                if group["id"] in known_groups:
                    action = "skipped"
                    if mode == "replace":
                        con.execute("UPDATE groups SET name=? WHERE id=?",
                                    (group["name"], group["id"]))
                        action = "replaced"
                else:
                    position = con.execute(
                        "SELECT COALESCE(MAX(position), -1) + 1 FROM groups").fetchone()[0]
                    con.execute(
                        "INSERT INTO groups (id, name, position, created_at) VALUES (?,?,?,?)",
                        (group["id"], group["name"], position, now))
                    known_groups.add(group["id"])
                    action = "created"
                group_report.append({"id": group["id"], "name": group["name"],
                                     "action": action})

            mapping = {e["id"]: new_id() for _, e in entries} if mode == "copy" else {}
            report: list[tuple[int, dict[str, Any]]] = []
            for pos, entry in sorted(entries, key=lambda pe: _ORDER[pe[1]["kind"]]):
                label = f"Eintrag {pos} ('{entry['name']}')"
                group_id = entry["group_id"] if entry["group_id"] in known_groups else None
                existing = con.execute("SELECT kind FROM definitions WHERE id=?",
                                       (entry["id"],)).fetchone()
                try:
                    if mode == "copy":
                        made = insert(con, kind=entry["kind"], name=entry["name"],
                                      description=entry["description"], group_id=group_id,
                                      spec=_rewrite(entry["spec"], mapping),
                                      def_id=mapping[entry["id"]], now=now)
                        action = "created"
                    elif existing is None:
                        made = insert(con, kind=entry["kind"], name=entry["name"],
                                      description=entry["description"], group_id=group_id,
                                      spec=entry["spec"], def_id=entry["id"], now=now)
                        action = "created"
                    elif mode == "skip":
                        made, action = fetch(con, entry["id"]), "skipped"
                    elif existing["kind"] != entry["kind"]:
                        raise InvalidDefinition(
                            f"'{entry['id']}' gibt es schon mit einer anderen Art "
                            f"({KIND_LABELS[existing['kind']]}).")
                    else:
                        made = replace(con, entry["id"], None, name=entry["name"],
                                       description=entry["description"], group_id=group_id,
                                       spec=entry["spec"], now=now)
                        action = "replaced"
                except InvalidDefinition as exc:
                    raise InvalidDefinition(f"{label}: {exc}") from None
                report.append((pos, {"source_id": entry["id"], "id": made.id,
                                     "kind": made.kind, "name": made.name, "action": action}))
            return {"mode": mode, "groups": group_report,
                    "definitions": [item for _, item in sorted(report, key=lambda r: r[0])]}

        return await self._db.write(fn)

    @staticmethod
    def _check_file(data: Any) -> tuple[list[dict[str, str]], list[tuple[int, dict[str, Any]]]]:
        """Alles prüfen, bevor irgendetwas geschrieben wird."""
        if not isinstance(data, dict) or data.get("format") != FORMAT:
            raise InvalidDefinition(
                "Das ist keine Export-Datei dieser Anwendung (format 'wda-definitions' fehlt).")
        if data.get("version") != VERSION:
            raise InvalidDefinition(
                f"Die Export-Datei hat eine andere Version; gelesen wird nur Version {VERSION}.")
        raw_groups, raw_defs = data.get("groups") or [], data.get("definitions")
        if not isinstance(raw_groups, list) or not isinstance(raw_defs, list):
            raise InvalidDefinition("'groups' und 'definitions' müssen Listen sein.")
        if len(raw_defs) > MAX_ENTRIES or len(raw_groups) > MAX_ENTRIES:
            raise InvalidDefinition(f"Zu viele Einträge (höchstens {MAX_ENTRIES}).")
        groups = []
        for pos, group in enumerate(raw_groups, 1):
            if (not isinstance(group, dict) or not isinstance(group.get("id"), str)
                    or not ID_PATTERN.fullmatch(group["id"])):
                raise InvalidDefinition(f"Gruppe {pos}: Die ID fehlt oder ist ungültig.")
            groups.append({"id": group["id"],
                           "name": clean_name(group.get("name"), f"Gruppe {pos}: Der Name")})
        entries, seen = [], set()
        for pos, raw in enumerate(raw_defs, 1):
            if not isinstance(raw, dict):
                raise InvalidDefinition(f"Eintrag {pos}: kein Objekt.")
            name = raw.get("name") if isinstance(raw.get("name"), str) else "?"
            shown = "".join(ch if ch.isprintable() else "?" for ch in name[:60])
            shown = shown.encode("utf-8", "replace").decode("utf-8")
            label = f"Eintrag {pos} ('{shown}')"
            def_id, kind = raw.get("id"), raw.get("kind")
            if not isinstance(def_id, str) or not ID_PATTERN.fullmatch(def_id):
                raise InvalidDefinition(f"{label}: Die ID fehlt oder ist ungültig.")
            if def_id in seen:
                raise InvalidDefinition(f"{label}: Die ID '{def_id}' kommt mehrfach vor.")
            seen.add(def_id)
            if kind not in KINDS:
                raise InvalidDefinition(f"{label}: Unbekannte Art.")
            try:
                parse_spec(kind, raw.get("spec"))
                clean = clean_name(raw.get("name"))
            except InvalidDefinition as exc:
                raise InvalidDefinition(f"{label}: {exc}") from None
            group_id = raw.get("group_id") if isinstance(raw.get("group_id"), str) else None
            entries.append((pos, {"id": def_id, "kind": kind, "name": clean,
                                  "description": raw.get("description"),
                                  "group_id": group_id, "spec": raw["spec"]}))
        return groups, entries


__all__ = ["NotFound", "Transfer"]
