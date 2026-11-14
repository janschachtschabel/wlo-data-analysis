"""Über Nacht vorrechnen: Schwere Auswertungen (Wissenslandkarte) werden einmal je Nacht neu
gezählt und als Lauf abgelegt; die Seite zeigt dann den abgelegten Lauf."""
from datetime import UTC, datetime, timedelta

from fastapi.testclient import TestClient

from wda.config import Settings
from wda.main import create_app

from .conftest import ADMIN_PASSWORD
from .esdata import DataRepo
from .test_definition_routes import QUERY, make


def app_with(tmp_path, repo, **more):
    settings = Settings(admin_password=ADMIN_PASSWORD, cookie_secure=False, data_dir=tmp_path,
                        scheduler=False, snapshot_nightly="03:30", **more)
    return create_app(settings, transport=repo.transport())


def test_listed_definitions_run_once_a_night(tmp_path):
    repo = DataRepo()
    with TestClient(app_with(tmp_path, repo, precompute="gibt-es-nicht, auch-nicht")) as api:
        assert api.post("/api/auth/login", json={"password": ADMIN_PASSWORD}).status_code == 204
        heavy = make(api, "metric", "Schwer", {"query": QUERY})["id"]
        services = api.app.state.services
        assert services.precompute == ["gibt-es-nicht", "auch-nicht"]
        services.precompute = ["gibt-es-nicht", heavy]
        now = datetime.now(UTC)
        # eine Auswertung, die es nicht gibt, hält die übrigen nicht auf
        assert api.portal.call(services.nightly_precompute, now) == [heavy]
        [run] = api.get(f"/api/definitions/{heavy}/runs").json()
        assert run["origin"] == "nightly" and run["status"] == "ok"
        # derselbe Tag: nichts mehr; die nächste Nacht: wieder
        assert api.portal.call(services.nightly_precompute, now) == []
        assert api.portal.call(services.nightly_precompute, now + timedelta(days=1)) == [heavy]
        assert len(api.get(f"/api/definitions/{heavy}/runs").json()) == 2


def test_a_failing_run_is_not_repeated_all_night_and_off_means_off(tmp_path, monkeypatch):
    repo = DataRepo()
    calls = []

    async def broken(self, def_id, **kwargs):
        calls.append(def_id)
        raise RuntimeError("kaputt")

    with TestClient(app_with(tmp_path, repo)) as api:
        assert api.post("/api/auth/login", json={"password": ADMIN_PASSWORD}).status_code == 204
        heavy = make(api, "metric", "Schwer", {"query": QUERY})["id"]
        services = api.app.state.services
        assert services.precompute == ["builtin-themen"]  # der Standard
        services.precompute = [heavy]
        monkeypatch.setattr("wda.store.runner.Runner.run", broken)
        now = datetime.now(UTC)
        assert api.portal.call(services.nightly_precompute, now) == []
        assert api.portal.call(services.nightly_precompute, now) == []
        assert calls == [heavy]
        monkeypatch.undo()
        # ausgeschaltet: keine Uhrzeit oder keine Liste
        services.precompute = []
        later = now + timedelta(days=3)
        assert api.portal.call(services.nightly_precompute, later) == []
    with TestClient(create_app(Settings(
            admin_password=ADMIN_PASSWORD, data_dir=tmp_path, scheduler=False,
            snapshot_nightly="off"), transport=repo.transport())) as api:
        assert api.post("/api/auth/login", json={"password": ADMIN_PASSWORD}).status_code == 204
        services = api.app.state.services
        services.precompute = [heavy]
        assert api.portal.call(services.nightly_precompute, later) == []
