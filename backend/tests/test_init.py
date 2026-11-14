"""Einrichtung im Container: Der erste Start gelingt ohne jede Variable. Das Admin-Passwort
entsteht zufällig im Datenverzeichnis; leere Variablen gelten als nicht gesetzt."""
import os
import stat

import pytest

from wda.config import ConfigError, load_settings
from wda.init import PASSWORD_FILE, main


@pytest.fixture
def env(monkeypatch, tmp_path):
    for key in list(os.environ):
        if key.startswith("WDA_") or key == "DOMAIN":
            monkeypatch.delenv(key)
    monkeypatch.setenv("WDA_DATA_DIR", str(tmp_path))
    return monkeypatch


def test_first_start_creates_a_password_and_keeps_it(env, tmp_path, capsys):
    assert main() == 0
    path = tmp_path / PASSWORD_FILE
    first = path.read_text().strip()
    assert len(first) >= 20 and stat.S_IMODE(path.stat().st_mode) == 0o600
    out = capsys.readouterr().out
    assert PASSWORD_FILE in out and first not in out  # das Passwort steht nie im Protokoll
    assert main() == 0 and path.read_text().strip() == first  # bleibt bei jedem Start gleich
    # die App liest es von dort, solange keines gesetzt ist
    env.setenv("WDA_ADMIN_PASSWORD_FILE", str(path))
    assert load_settings().admin_password.get_secret_value() == first
    env.setenv("WDA_ADMIN_PASSWORD", "")
    assert load_settings().admin_password.get_secret_value() == first
    # ein gesetztes Passwort geht vor
    env.setenv("WDA_ADMIN_PASSWORD", "selbst-gewaehlt")
    assert load_settings().admin_password.get_secret_value() == "selbst-gewaehlt"


def test_a_set_password_needs_no_file(env, tmp_path):
    env.setenv("WDA_ADMIN_PASSWORD", "selbst-gewaehlt")
    assert main() == 0 and not (tmp_path / PASSWORD_FILE).exists()


def test_domain_is_a_bare_host_name(env, capsys):
    for good in ("", "daten.example.org", "srv123456.hstgr.cloud"):
        env.setenv("DOMAIN", good)
        assert main() == 0
    for bad in ("https://daten.example.org", "daten.example.org/pfad", "daten.example.org:443",
                "mit leerzeichen.org"):
        env.setenv("DOMAIN", bad)
        assert main() == 2, bad
        assert "DOMAIN" in capsys.readouterr().err


def test_empty_variables_count_as_unset(env, tmp_path):
    """Die mitgelieferte .env hat leere Felder; keines davon darf den Start verhindern."""
    env.setenv("WDA_ADMIN_PASSWORD", "x")
    for key in ("WDA_VIEWER_PASSWORD", "WDA_SESSION_SECRET", "WDA_LLM_API_KEY", "WDA_INSTANCES",
                "WDA_SMTP_HOST", "WDA_SMTP_USER", "WDA_SMTP_PASSWORD", "WDA_SMTP_FROM",
                "WDA_PUBLIC_URL", "WDA_SMTP_PORT", "WDA_COOKIE_SECURE", "WDA_LLM_MODEL",
                "WDA_LLM_BASE_URL", "WDA_PRECOMPUTE", "WDA_TIMEZONE", "WDA_SNAPSHOT_NIGHTLY"):
        env.setenv(key, "")
    settings = load_settings()
    assert settings.viewer_password is None and settings.smtp_port == 587
    assert settings.llm_model == "gpt-6-luna" and settings.cookie_secure is True
    assert settings.timezone == "Europe/Berlin"
    # nur wer das Vorrechnen und das Nachziehen abschalten will, schreibt „off“
    assert settings.precompute == "builtin-themen" and settings.snapshot_nightly == "03:30"
    env.setenv("WDA_ADMIN_PASSWORD_FILE", str(tmp_path / "fehlt"))
    env.setenv("WDA_ADMIN_PASSWORD", "")
    with pytest.raises(ConfigError, match="WDA_ADMIN_PASSWORD"):
        load_settings()
