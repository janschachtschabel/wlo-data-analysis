from pathlib import Path

import pytest

from wda.config import ConfigError, load_settings


def test_defaults(monkeypatch):
    for key in ("WDA_LLM_BASE_URL", "WDA_LLM_MODEL", "WDA_DATA_DIR", "WDA_INSTANCES"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("WDA_ADMIN_PASSWORD", "geheim")
    s = load_settings()
    assert s.admin_password.get_secret_value() == "geheim"
    assert s.llm_base_url == "https://api.openai.com/v1"
    assert s.llm_model == "gpt-6-luna"
    assert s.data_dir == Path("/data")
    assert s.cookie_secure is True
    assert s.session_max_age == 43200


@pytest.mark.parametrize("value", [None, "", "   "])
def test_missing_password_is_config_error(monkeypatch, value):
    if value is None:
        monkeypatch.delenv("WDA_ADMIN_PASSWORD", raising=False)
    else:
        monkeypatch.setenv("WDA_ADMIN_PASSWORD", value)
    with pytest.raises(ConfigError, match="WDA_ADMIN_PASSWORD"):
        load_settings()


def test_env_overrides(monkeypatch):
    monkeypatch.setenv("WDA_ADMIN_PASSWORD", "x")
    monkeypatch.setenv("WDA_LLM_BASE_URL", "https://b-api.example/api/v1/llm/openai/")
    monkeypatch.setenv("WDA_LLM_MODEL", "anderes")
    monkeypatch.setenv("WDA_COOKIE_SECURE", "false")
    s = load_settings()
    assert s.llm_base_url == "https://b-api.example/api/v1/llm/openai"
    assert s.llm_model == "anderes"
    assert s.cookie_secure is False


def test_vocab_ttl_has_a_minimum(monkeypatch):
    monkeypatch.setenv("WDA_ADMIN_PASSWORD", "x")
    monkeypatch.setenv("WDA_VOCAB_TTL", "0")
    with pytest.raises(ConfigError, match="WDA_VOCAB_TTL"):
        load_settings()


def test_secrets_not_in_repr(monkeypatch):
    monkeypatch.setenv("WDA_ADMIN_PASSWORD", "supergeheim")
    monkeypatch.setenv("WDA_LLM_API_KEY", "sk-abc")
    text = repr(load_settings())
    assert "supergeheim" not in text and "sk-abc" not in text
