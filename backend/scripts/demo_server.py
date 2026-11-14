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

from tests.esdata import LEVELS, SUBJECTS, DataRepo, make_docs  # noqa: E402

THEMES = {"Mathematik": ["Bruchrechnung", "Geometrie", "Gleichungen", "Prozentrechnung"],
          "Physik": ["Mechanik", "Optik", "Elektrizität", "Energie"],
          "Deutsch": ["Grammatik", "Märchen", "Rechtschreibung", "Lyrik"],
          "Geschichte": ["Weimarer Republik", "Mittelalter", "Antike", "Kalter Krieg"],
          "Informatik": ["Programmieren", "Algorithmen", "Datenbanken", "Netzwerke"]}
BY_LEVEL = {"Elementarbereich": "Spielen", "Primarstufe": "Sachunterricht",
            "Sekundarstufe I": "Projektarbeit", "Hochschule": "Vorlesung"}


def demo_docs() -> list:
    """Der Testbestand mit Schlagwörtern, damit die Wissenslandkarte etwas zeigt."""
    docs = make_docs()
    names = {uri: name for name, uri in SUBJECTS.items()}
    levels = {uri: name for name, uri in LEVELS.items()}
    for n, doc in enumerate(docs):
        words = ["Nachhilfe", "einfach erklärt"]
        for uri in doc["ccm:taxonid"]:
            subject = names.get(uri)
            if subject:
                found = THEMES[subject]
                words += [subject, found[n % 4], found[(n // 4) % 4].lower()]
        for uri in doc["ccm:educationalcontext"]:
            if uri in levels and n % 2:
                words.append(BY_LEVEL.get(levels[uri], "Übung"))
        doc["cclom:general_keyword"] = list(dict.fromkeys(words))
    return docs
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
    if last["role"] == "user" and "bericht" in last["content"].lower():
        return _call("propose_template", {"name": "Bestandsbericht", "sections": [
            {"type": "text", "markdown": "# Bestand\n\nStand {{run.date}}, gesamt "
                                         "{{a2.total.bestand.gesamt}} Materialien."},
            {"type": "matrix", "title": "Fächer nach Gruppen", "rows": {
                "property": "ccm:taxonid", "groups": [
                    {"label": "MINT", "values": ["Mathematik", "Physik", "Informatik"]},
                    {"label": "Sprachen", "values": ["Deutsch"]}]},
             "columns": [{"id": "gesamt", "label": "Gesamt"}],
             "blocks": [{"label": "Bestand"}]},
            {"type": "chart", "title": "Bildungsstufen", "view": "bar",
             "spec": {"rows": {"property": "ccm:educationalcontext"}}}]})
    if last["role"] == "user":
        spec = {"rows": {"property": "ccm:taxonid"},
                "columns": {"property": "ccm:educationalcontext"}}
        return _call("propose_view", {"title": "Fach × Bildungsstufe", "view": "heatmap",
                                      "spec": spec})
    return _say("Hier ist die Ansicht. **Mathematik** ist gut abgedeckt.\n\n"
                "- Lücken sind rötlich hinterlegt.")


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
    app = create_app(settings, transport=DataRepo(demo_docs()).transport(),
                     llm_transport=httpx.MockTransport(demo_model))
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning")


if __name__ == "__main__":
    main()
