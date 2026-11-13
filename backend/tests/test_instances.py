import json

import pytest

from wda.config import ConfigError
from wda.instances import Instance, InstanceRegistry


def test_presets():
    reg = InstanceRegistry(None)
    ids = [i.id for i in reg.list()]
    assert ids[:2] == ["wlo-prod", "wlo-staging"]
    prod = reg.get("wlo-prod")
    assert prod.base_url == "https://redaktion.openeduhub.net"
    assert prod.rest_url == "https://redaktion.openeduhub.net/edu-sharing/rest"
    assert (prod.repository, prod.metadataset, prod.query) == ("-home-", "mds_oeh", "ngsearch")
    assert reg.get("wlo-staging").base_url == "https://repository.staging.openeduhub.net"


def test_config_error_does_not_echo_url_secrets():
    extra = json.dumps([{"id": "a", "name": "A", "base_url": "https://user:topsecret@a.example"}])
    with pytest.raises(ConfigError) as info:
        InstanceRegistry(extra)
    assert "topsecret" not in str(info.value)


def test_unknown_instance():
    with pytest.raises(KeyError):
        InstanceRegistry(None).get("gibt-es-nicht")


def test_public_hides_credentials():
    inst = Instance(id="x", name="X", base_url="https://e.example/", username="u", password="p4ss")
    pub = inst.public()
    assert inst.base_url == "https://e.example"
    assert pub["has_credentials"] is True
    assert "password" not in pub and "username" not in pub
    assert "p4ss" not in json.dumps(pub)


def test_extra_instance_and_override():
    extra = json.dumps([
        {"id": "eigene", "name": "Eigene", "base_url": "https://edu.example.org",
         "metadataset": "mds", "username": "a", "password": "b"},
        {"id": "wlo-prod", "name": "Prod neu", "base_url": "https://prod.example.org"},
    ])
    reg = InstanceRegistry(extra)
    assert [i.id for i in reg.list()] == ["wlo-prod", "wlo-staging", "eigene"]
    assert reg.get("wlo-prod").base_url == "https://prod.example.org"
    assert reg.get("eigene").metadataset == "mds"
    assert reg.get("eigene").public()["has_credentials"] is True


@pytest.mark.parametrize("extra", [
    "{kaputt",
    json.dumps({"id": "a"}),
    json.dumps([{"id": "a", "name": "A", "base_url": "https://a.example"},
                {"id": "a", "name": "B", "base_url": "https://b.example"}]),
    json.dumps([{"id": "a", "name": "A", "base_url": "ftp://a.example"}]),
    json.dumps([{"id": "Groß Schreibung", "name": "A", "base_url": "https://a.example"}]),
    json.dumps([{"id": "a", "name": "A", "base_url": "https://"}]),
    json.dumps([{"id": "a", "name": "A"}]),
    json.dumps([{"id": "a", "name": "A", "base_url": "https://user:topsecret@a.example"}]),
    json.dumps([{"id": "a", "name": "A", "base_url": "https://a.example/?token=abc"}]),
    json.dumps([{"id": "a", "name": "A", "base_url": "https://a.example",
                 "search_url": "https://u@s.example"}]),
])
def test_invalid_extra_is_config_error(extra):
    with pytest.raises(ConfigError, match="WDA_INSTANCES"):
        InstanceRegistry(extra)
