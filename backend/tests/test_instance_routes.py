"""Instanzen in der Oberfläche anlegen, ändern und entfernen."""

import json

import pytest
from fastapi.testclient import TestClient

from wda.main import create_app

from .conftest import ADMIN_PASSWORD, INSTANCE_PASSWORD

NEW = {"id": "kita", "name": "Kita Bayern", "base_url": "https://kita.example.org/"}


def by_id(client):
    return {i["id"]: i for i in client.get("/api/instances").json()}


def test_list_names_where_an_instance_comes_from(admin):
    items = by_id(admin)
    assert items["wlo-prod"]["source"] == "preset"
    assert items["eigene"]["source"] == "config"
    assert all(i["overridden"] is False for i in items.values())


def test_create_update_delete(admin):
    made = admin.post("/api/instances", json={**NEW, "username": "u", "password": "geheim-1"})
    assert made.status_code == 201, made.text
    body = made.json()
    assert body["base_url"] == "https://kita.example.org"
    assert (body["source"], body["has_credentials"]) == ("stored", True)
    assert body["metadataset"] == "mds_oeh"
    assert "geheim-1" not in made.text and "password" not in body and "username" not in body
    assert list(by_id(admin))[-1] == "kita"

    assert admin.post("/api/instances", json=NEW).status_code == 409

    # Passwort weglassen heißt behalten
    changed = admin.put("/api/instances/kita", json={
        "name": "Kita", "base_url": "https://kita.example.org", "username": "u"})
    assert changed.status_code == 200, changed.text
    assert changed.json()["name"] == "Kita" and changed.json()["has_credentials"] is True
    assert admin.app.state.services.registry.get("kita").password.get_secret_value() == "geheim-1"

    # ohne Benutzer sind auch die Zugangsdaten weg
    plain = admin.put("/api/instances/kita", json={"name": "Kita", "base_url": "https://kita.example.org"})
    assert plain.json()["has_credentials"] is False
    assert admin.app.state.services.registry.get("kita").password is None

    assert admin.delete("/api/instances/kita").status_code == 204
    assert "kita" not in by_id(admin)
    gone = admin.delete("/api/instances/kita")
    assert (gone.status_code, gone.json()["kind"]) == (404, "unknown_instance")


def test_stored_instances_survive_a_restart(admin, settings):
    admin.post("/api/instances", json={**NEW, "username": "u", "password": "geheim-1"})
    admin.app.state.services.db.close()
    again = TestClient(create_app(settings))
    assert again.post("/api/auth/login", json={"password": ADMIN_PASSWORD}).status_code == 204
    assert by_id(again)["kita"]["has_credentials"] is True


def test_fixed_instances_can_be_overridden_but_not_deleted(admin):
    fixed = admin.delete("/api/instances/wlo-prod")
    assert (fixed.status_code, fixed.json()["kind"]) == (409, "conflict")
    over = admin.put("/api/instances/wlo-prod", json={
        "name": "WLO mit Anmeldung", "base_url": "https://redaktion.openeduhub.net",
        "username": "redakteur", "password": "geheim-2"})
    assert over.status_code == 200
    item = by_id(admin)["wlo-prod"]
    assert (item["name"], item["source"]) == ("WLO mit Anmeldung", "stored")
    assert item["overridden"] is True
    assert list(by_id(admin))[0] == "wlo-prod"  # die Reihenfolge bleibt
    # Entfernen stellt die feste Fassung wieder her
    assert admin.delete("/api/instances/wlo-prod").status_code == 204
    item = by_id(admin)["wlo-prod"]
    assert (item["name"], item["source"], item["overridden"]) == ("WLO Produktion", "preset", False)


def test_override_of_a_configured_instance_keeps_its_password_when_omitted(admin):
    admin.put("/api/instances/eigene", json={
        "name": "Eigene 2", "base_url": "https://edu.example.org", "username": "nutzer-789"})
    inst = admin.app.state.services.registry.get("eigene")
    assert inst.password.get_secret_value() == INSTANCE_PASSWORD
    # die Oberfläche kennt den Benutzernamen nicht und bittet nur ums Behalten
    admin.put("/api/instances/eigene", json={
        "name": "Eigene 3", "base_url": "https://edu.example.org", "keep_credentials": True})
    inst = admin.app.state.services.registry.get("eigene")
    assert (inst.username, inst.password.get_secret_value()) == ("nutzer-789", INSTANCE_PASSWORD)
    kept = admin.put("/api/instances/eigene", json={
        "name": "Eigene 3", "base_url": "https://woanders.example.org", "keep_credentials": True})
    assert kept.status_code == 422
    # anderer Host: das alte Passwort geht nicht dorthin mit
    moved = admin.put("/api/instances/eigene", json={
        "name": "Eigene 2", "base_url": "https://woanders.example.org", "username": "nutzer-789"})
    assert moved.status_code == 422 and "Passwort" in moved.json()["detail"]


@pytest.mark.parametrize("change, where", [
    ({"base_url": "ftp://x.example"}, "base_url"),
    ({"base_url": "https://u:pw-im-link@x.example"}, "base_url"),
    ({"base_url": "http://offen.example", "username": "u", "password": "streng-geheim"}, "https"),
    ({"id": "Groß"}, "id"),
    ({"unbekannt": 1}, "unbekannt"),
    ({"name": "  "}, "name"),
    ({"facet_fields": ["cclom:title"]}, "facet_fields"),
    ({"prefix_fields": ["ccm:x"]}, "prefix_fields"),
    ({"base_url": "https://x.example:abc"}, "base_url"),
    ({"base_url": "https://x.exa\tmple"}, "base_url"),
    ({"base_url": " https://x.example"}, "base_url"),
])
def test_invalid_input_is_explained_without_echoing_secrets(admin, change, where):
    r = admin.post("/api/instances", json={**NEW, **change})
    assert r.status_code == 422, r.text
    assert where in r.json()["detail"]
    assert "streng-geheim" not in r.text and "pw-im-link" not in r.text
    assert "kita" not in by_id(admin)


def test_change_drops_client_cache_and_vocabulary_copy(admin, settings):
    admin.post("/api/instances", json=NEW)
    services = admin.app.state.services
    first = services.client("kita")
    services.vocab("kita")
    copy = settings.data_dir / "cache" / "mds-kita.json"
    copy.parent.mkdir(parents=True, exist_ok=True)
    copy.write_text(json.dumps({"alt": True}), encoding="utf-8")
    services.cache._entries[("kita", "x")] = (10**12, "alt")

    admin.put("/api/instances/kita", json={"name": "Kita", "base_url": "https://neu.example.org"})
    assert services.client("kita") is not first
    assert services.client("kita").instance.base_url == "https://neu.example.org"
    assert not copy.exists()
    assert len(services.cache) == 0

    services.client("kita")
    admin.delete("/api/instances/kita")
    with pytest.raises(KeyError):
        services.client("kita")


def test_routes_of_a_removed_instance_answer_unknown_instance(admin):
    admin.post("/api/instances", json=NEW)
    admin.delete("/api/instances/kita")
    r = admin.get("/api/instances/kita/fields")
    assert (r.status_code, r.json()["kind"]) == (404, "unknown_instance")


def test_limit_on_number_of_instances(admin):
    for n in range(50):
        r = admin.post("/api/instances", json={**NEW, "id": f"i{n}"})
        if r.status_code != 201:
            break
    assert r.status_code == 409 and "höchstens" in r.json()["detail"]


def test_surface_cannot_widen_what_may_be_broken_down(admin, settings):
    """Die Freigabe von Feldern ohne Werteliste (Titel, URL …) wäre eine Datensatzliste; sie
    bleibt Sache der Betriebskonfiguration und übersteht jede Änderung in der Oberfläche."""
    registry = admin.app.state.services.registry
    before = registry.get("wlo-prod")
    r = admin.put("/api/instances/wlo-prod", json={
        "name": "X", "base_url": "https://redaktion.openeduhub.net",
        "facet_fields": ["cclom:title"]})
    assert r.status_code == 422
    assert admin.put("/api/instances/wlo-prod", json={
        "name": "X", "base_url": "https://redaktion.openeduhub.net"}).status_code == 200
    after = registry.get("wlo-prod")
    assert (after.facet_fields, after.prefix_fields) == (before.facet_fields,
                                                         before.prefix_fields)


def test_configured_fields_survive_an_edit(settings):
    import json as _json
    custom = settings.model_copy(update={"instances": _json.dumps([{
        "id": "eigene", "name": "E", "base_url": "https://edu.example.org",
        "facet_fields": [], "prefix_fields": ["ccm:x", "ccm:y"]}])})
    client = TestClient(create_app(custom))
    assert client.post("/api/auth/login", json={"password": ADMIN_PASSWORD}).status_code == 204
    assert client.put("/api/instances/eigene", json={
        "name": "E2", "base_url": "https://edu.example.org"}).status_code == 200
    inst = client.app.state.services.registry.get("eigene")
    assert (inst.name, inst.facet_fields, inst.prefix_fields) == ("E2", (), ("ccm:x", "ccm:y"))


def test_limit_is_exactly_max_stored(admin):
    from wda.instances import MAX_STORED
    codes = [admin.post("/api/instances", json={**NEW, "id": f"i{n}"}).status_code
             for n in range(MAX_STORED + 1)]
    assert codes == [201] * MAX_STORED + [409]
