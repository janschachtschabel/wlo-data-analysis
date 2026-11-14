"""Startet das Backend mit einem nachgebauten Repositorium (Testbestand) statt WLO.

Für die Entwicklung und die Durchstiche der Oberfläche; nie für den Betrieb.
Aufruf:  python scripts/demo_server.py [port]      Passwort: demo-passwort
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

import httpx
import uvicorn

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tests.esdata import DataRepo  # noqa: E402
from wda.config import Settings  # noqa: E402
from wda.main import create_app  # noqa: E402

PASSWORD = "demo-passwort"  # noqa: S105 (nur Demo mit nachgebautem Bestand, nie Betrieb)


def demo_model(request: httpx.Request) -> httpx.Response:
    """Gespieltes Sprachmodell für die Durchstiche: zählt Fächer je Bildungsstufe, schlägt
    die Ansicht vor und antwortet mit einem festen Satz."""
    body = json.loads(request.content)
    last = body["messages"][-1]
    if "tools" not in body:
        return _say("bereit")
    if last["role"] == "user" and "dashboard" in last["content"].lower():
        return _call("propose_dashboard", {"name": "Überblick", "description": "Bestand",
                                           "tiles": [
            {"title": "Fächer", "spec": {"rows": {"property": "ccm:taxonid"}}, "view": "bar",
             "width": "half"},
            {"title": "Bildungsstufen", "spec": {"rows": {"property": "ccm:educationalcontext"}},
             "view": "pie", "width": "half"}]})
    if last["role"] == "user":
        spec = {"rows": {"property": "ccm:taxonid"},
                "columns": {"property": "ccm:educationalcontext"}}
        return _call("propose_view", {"title": "Fach × Bildungsstufe", "view": "heatmap",
                                      "spec": spec})
    return _say("Hier ist die Ansicht. **Mathematik** ist gut abgedeckt.\n\n"
                "- Lücken sind schraffiert.")


def _say(text: str) -> httpx.Response:
    return httpx.Response(200, json={"choices": [{"message": {"content": text}}]})


def _call(name: str, args: dict) -> httpx.Response:
    return httpx.Response(200, json={"choices": [{"message": {"content": None, "tool_calls": [
        {"id": "c1", "type": "function",
         "function": {"name": name, "arguments": json.dumps(args)}}]}}]})


def main() -> None:
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8765
    settings = Settings(admin_password=PASSWORD, cookie_secure=False,
                        data_dir=Path(tempfile.mkdtemp(prefix="wda-demo-")),
                        llm_api_key="demo", llm_settings_ui=True)
    app = create_app(settings, transport=DataRepo().transport(),
                     llm_transport=httpx.MockTransport(demo_model))
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning")


if __name__ == "__main__":
    main()
