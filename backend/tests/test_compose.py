"""Der Produktiv-Stack hält die Regeln für den Docker Manager von Hostinger ein: Der erste
Start gelingt ohne jede Angabe, es wird nicht gebaut, Traefik bedient die Ports."""
import re
from pathlib import Path

from wda.config import Settings

ROOT = Path(__file__).resolve().parents[2]
COMPOSE = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")
EXAMPLE = (ROOT / ".env.example").read_text(encoding="utf-8")
CODE = "\n".join(line for line in COMPOSE.splitlines() if not line.lstrip().startswith("#"))


def test_every_variable_has_a_default_and_nothing_is_required():
    assert re.findall(r"\$\{[^}]*\}", CODE)
    for use in re.findall(r"\$\{[^}]*\}", CODE):
        assert re.fullmatch(r"\$\{[A-Z_]+:-[^${}]*\}", use), use
    assert not re.search(r"\$[A-Za-z_]", CODE) and "$$" not in CODE


def test_no_build_no_ports_no_bind_mounts_no_external_network():
    for word in ("build:", "external:", "\t", "<<:", "&", "env_file:", ":80\"", ":443\""):
        assert word not in CODE, word
    # genau ein Port, für den Betrieb ohne Domain; ohne Angabe nur auf dem Server selbst
    assert CODE.count("ports:") == 1
    assert '- "${HOST_PORT:-127.0.0.1:18080}:${WDA_PORT:-8080}"' in CODE
    for mount in re.findall(r"^\s+- ([^\s:]+):/", CODE, flags=re.M):
        assert re.fullmatch(r"[a-z-]+", mount), mount  # benannte Volumes, keine Pfade
    assert "name: ${TRAEFIK_NETWORK:-wlo-data-analysis-web}" in CODE
    assert "ghcr.io/janschachtschabel/wlo-data-analysis" in CODE


def test_traefik_labels_and_init_service():
    for label in ("traefik.enable=${TRAEFIK_ENABLE:-true}", "traefik.docker.network=",
                  "routers.wlo-data-analysis.rule=Host(`${DOMAIN:-}`)",
                  "entrypoints=${TRAEFIK_ENTRYPOINT:-websecure}",
                  "tls.certresolver=${TRAEFIK_CERTRESOLVER:-letsencrypt}",
                  "loadbalancer.server.port=${WDA_PORT:-8080}"):
        assert label in CODE, label
    assert "condition: service_completed_successfully" in CODE and "restart: true" in CODE
    assert "WDA_ADMIN_PASSWORD_FILE: /data/admin.password" in CODE


def test_env_example_lists_every_variable_empty_and_names_match_the_settings():
    used = set(re.findall(r"\$\{([A-Z_]+):-", CODE))
    listed = dict(re.findall(r"^([A-Z_]+)=(.*)$", EXAMPLE, flags=re.M))
    assert used == set(listed), used ^ set(listed)
    assert all(value == "" for value in listed.values()), listed  # keine wirkenden Platzhalter
    fields = {"WDA_" + name.upper() for name in Settings.model_fields}
    passed = set(re.findall(r"^\s+(WDA_[A-Z_]+):", CODE, flags=re.M)) - {
        "WDA_ADMIN_PASSWORD_FILE", "WDA_PORT"}
    assert passed <= fields, passed - fields


def test_port_is_one_setting_for_app_traefik_and_healthcheck():
    assert "WDA_PORT: ${WDA_PORT:-8080}" in CODE
    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    assert "os.environ.get('WDA_PORT') or '8080'" in dockerfile
    build = (ROOT / "docker-compose.build.yml").read_text(encoding="utf-8")
    assert '"${WDA_PORT:-8080}:${WDA_PORT:-8080}"' in build


def test_start_reads_the_port(monkeypatch):
    import pytest

    from wda.__main__ import _port
    from wda.config import ConfigError
    assert _port(None) == 8080 and _port("") == 8080 and _port(" 8091 ") == 8091
    for wrong in ("0", "70000", "acht", "-1"):
        with pytest.raises(ConfigError):
            _port(wrong)
