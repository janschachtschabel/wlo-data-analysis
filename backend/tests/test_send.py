"""Versand nach Zeitplan: Nach dem Lauf geht der Bericht per E-Mail und an einen Webhook.
Verschickt werden Auswertungen, nie Datensätze; ein gescheiterter Versand lässt den Lauf gültig."""
import io
import zipfile

import pytest
from fastapi.testclient import TestClient

from wda.config import Settings
from wda.main import create_app

from .conftest import ADMIN_PASSWORD
from .esdata import DataRepo
from .test_definition_routes import QUERY, make


@pytest.fixture
def repo():
    return DataRepo()


@pytest.fixture
def api(tmp_path, repo):
    settings = Settings(admin_password=ADMIN_PASSWORD, cookie_secure=False, data_dir=tmp_path,
                        scheduler=False, smtp_host="mail.example.org",
                        smtp_from="dashboard@example.org", public_url="https://dash.example.org")
    with TestClient(create_app(settings, transport=repo.transport())) as client:
        assert client.post("/api/auth/login",
                           json={"password": ADMIN_PASSWORD}).status_code == 204
        notifier = client.app.state.services.notifier
        client.mails, client.posts = [], []
        notifier.send_mail = client.mails.append

        async def post(url, payload):
            client.posts.append((url, payload))

        notifier.post = post
        yield client


def schedule(api, def_id, send):
    out = api.post("/api/schedules", json={"definition_id": def_id, "every": "daily",
                                           "at": "06:00", "send": send})
    assert out.status_code == 201, out.text
    return out.json()


def execute(api, item):
    scheduler = api.app.state.scheduler

    async def go():
        return await scheduler.execute(item, advance=False)

    run = api.portal.call(go)
    assert run and run["status"] == "ok"
    return api.get(f"/api/schedules?definition={item['definition_id']}").json()[0]


def test_report_goes_out_by_mail_and_webhook(api):
    m = make(api, "metric", "Fächer im Bestand", {"query": QUERY})
    item = schedule(api, m["id"], {"to": ["a@example.org", "b@example.org"], "format": "docx",
                                   "webhook": "https://hooks.example.org/x"})
    assert item["send"] == {"to": ["a@example.org", "b@example.org"], "format": "docx",
                            "webhook": "https://hooks.example.org/x", "only_notable": False}
    after = execute(api, item)
    assert after["last_send"] == "Verschickt an 2 Empfänger und den Webhook."
    [mail] = api.mails
    assert mail["To"] == "a@example.org, b@example.org"
    assert mail["From"] == "dashboard@example.org" and "Fächer im Bestand" in mail["Subject"]
    body = mail.get_body(("plain",)).get_content()
    assert f"https://dash.example.org/lauf/{after['last_run_id']}" in body
    [attachment] = list(mail.iter_attachments())
    assert attachment.get_filename().endswith(".docx")
    archive = zipfile.ZipFile(io.BytesIO(attachment.get_content()))
    assert "Mathematik" in archive.read("word/document.xml").decode()
    [(url, payload)] = api.posts
    assert url == "https://hooks.example.org/x"
    assert payload["title"] == "Fächer im Bestand" and payload["status"] == "ok"
    assert payload["run_id"] == after["last_run_id"] and payload["url"].endswith(
        after["last_run_id"])
    assert payload["missed_targets"] == [] and payload["changes"] == []
    assert "rows" not in str(payload) and "blocks" not in payload  # keine Tabellen


def test_only_when_something_is_notable(api, repo):
    quiet = make(api, "metric", "Ruhig", {"query": QUERY})
    item = schedule(api, quiet["id"], {"to": ["a@example.org"], "only_notable": True})
    after = execute(api, item)
    assert api.mails == [] and after["last_send"] == "Nichts Auffälliges, nicht verschickt."
    # ein verfehlter Zielwert ist auffällig und steht in der Nachricht
    goal = make(api, "metric", "Mit Ziel", {"query": {"instance": "wlo-prod"}, "view": {
        "type": "number", "options": {"target": 100000}}})
    second = execute(api, schedule(api, goal["id"], {"to": ["a@example.org"],
                                                     "only_notable": True}))
    assert second["last_send"] == "Verschickt an 1 Empfänger."
    body = api.mails[0].get_body(("plain",)).get_content()
    assert "Nicht erreicht: 400" in body
    # eine auffällige Veränderung seit dem vorigen Lauf ebenso
    api.mails.clear()
    repo.docs.extend([dict(d) for d in repo.docs[:200]])
    third = execute(api, item)
    assert third["last_send"] == "Verschickt an 1 Empfänger."
    assert "Auffällige Veränderungen seit dem Lauf vom" in api.mails[0].get_body(
        ("plain",)).get_content()


def test_a_failed_delivery_leaves_the_run_valid(api):
    m = make(api, "metric", "Fächer", {"query": QUERY})
    item = schedule(api, m["id"], {"to": ["a@example.org"],
                                   "webhook": "https://hooks.example.org/x"})

    def broken(message):
        raise OSError("Verbindung abgelehnt")

    api.app.state.services.notifier.send_mail = broken
    after = execute(api, item)
    assert after["last_status"] == "ok" and after["last_run_id"]
    assert after["last_send"] == (
        "Verschickt an den Webhook. Versand gescheitert (E-Mail: keine Verbindung).")
    assert len(api.posts) == 1  # der Webhook ging trotzdem


def test_send_is_validated_and_settings_say_whether_mail_works(api, tmp_path):
    m = make(api, "metric", "Fächer", {"query": QUERY})
    for bad in ({"to": ["kein-at"]}, {"to": ["a@example.org\nBcc: x@example.org"]},
                {"webhook": "ftp://example.org/x"}, {"webhook": "https://user:pw@example.org/"},
                {"to": [], "webhook": None}, {"to": ["a@example.org"], "format": "exe"},
                {"to": [f"a{n}@example.org" for n in range(11)]}):
        out = api.post("/api/schedules", json={"definition_id": m["id"], "every": "daily",
                                               "at": "06:00", "send": bad})
        assert out.status_code == 422, (bad, out.text)
    assert api.get("/api/settings").json()["mail"] == {"ready": True}
    item = schedule(api, m["id"], {"to": ["a@example.org"]})
    changed = api.put(f"/api/schedules/{item['id']}", json={
        "every": "daily", "at": "07:00", "send": None})
    assert changed.status_code == 200 and changed.json()["send"] is None
    # ohne Mailserver: der Zeitplan sagt, woran es liegt
    settings = Settings(admin_password=ADMIN_PASSWORD, cookie_secure=False,
                        data_dir=tmp_path / "ohne", scheduler=False)
    with TestClient(create_app(settings, transport=DataRepo().transport())) as plain:
        assert plain.post("/api/auth/login",
                          json={"password": ADMIN_PASSWORD}).status_code == 204
        assert plain.get("/api/settings").json()["mail"] == {"ready": False}
        m = make(plain, "metric", "Fächer", {"query": QUERY})
        after = execute(plain, schedule(plain, m["id"], {"to": ["a@example.org"]}))
        assert "kein Mailserver" in after["last_send"]


def test_missed_targets_show_up_among_the_highlights(api):
    make(api, "metric", "Ohne Ziel", {"query": QUERY})
    goal = make(api, "metric", "Mit Ziel", {"query": {"instance": "wlo-prod"}, "view": {
        "type": "number", "options": {"target": 100000}}})
    run = api.post(f"/api/definitions/{goal['id']}/run").json()
    [target] = api.get("/api/highlights").json()["targets"]
    assert target["definition_id"] == goal["id"] and target["run_id"] == run["id"]
    assert target["name"] == "Mit Ziel" and "Nicht erreicht: 400" in target["text"]
