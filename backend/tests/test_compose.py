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
    for word in ("build:", "ports:", "external:", "\t", "<<:", "&", "env_file:"):
        assert word not in CODE, word
    for mount in re.findall(r"^\s+- ([^\s:]+):/", CODE, flags=re.M):
        assert re.fullmatch(r"[a-z-]+", mount), mount  # benannte Volumes, keine Pfade
    assert "name: ${TRAEFIK_NETWORK:-wlo-data-analysis-web}" in CODE
    assert "ghcr.io/janschachtschabel/wlo-data-analysis" in CODE


def test_traefik_labels_and_init_service():
    for label in ("traefik.enable=true", "traefik.docker.network=",
                  "routers.wlo-data-analysis.rule=Host(`${DOMAIN:-}`)",
                  "entrypoints=${TRAEFIK_ENTRYPOINT:-websecure}",
                  "tls.certresolver=${TRAEFIK_CERTRESOLVER:-letsencrypt}",
                  "loadbalancer.server.port=8080"):
        assert label in CODE, label
    assert "condition: service_completed_successfully" in CODE and "restart: true" in CODE
    assert "WDA_ADMIN_PASSWORD_FILE: /data/admin.password" in CODE


def test_env_example_lists_every_variable_empty_and_names_match_the_settings():
    used = set(re.findall(r"\$\{([A-Z_]+):-", CODE))
    listed = dict(re.findall(r"^([A-Z_]+)=(.*)$", EXAMPLE, flags=re.M))
    assert used == set(listed), used ^ set(listed)
    assert all(value == "" for value in listed.values()), listed  # keine wirkenden Platzhalter
    fields = {"WDA_" + name.upper() for name in Settings.model_fields}
    passed = set(re.findall(r"^\s+(WDA_[A-Z_]+):", CODE, flags=re.M)) - {"WDA_ADMIN_PASSWORD_FILE"}
    assert passed <= fields, passed - fields
