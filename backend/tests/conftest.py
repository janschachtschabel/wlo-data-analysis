import json

import pytest
from fastapi.testclient import TestClient

from wda.config import Settings
from wda.main import create_app

ADMIN_PASSWORD = "pässwört ✓ " + "x" * 500
LLM_KEY = "sk-test-llm-key-123"
INSTANCE_PASSWORD = "instanz-geheimnis-456"


class FakeClock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


@pytest.fixture
def settings(tmp_path):
    return Settings(
        admin_password=ADMIN_PASSWORD,
        cookie_secure=False,
        data_dir=tmp_path,
        llm_api_key=LLM_KEY,
        llm_settings_ui=True,
        instances=json.dumps([
            {"id": "eigene", "name": "Eigene", "base_url": "https://edu.example.org",
             "username": "nutzer-789", "password": INSTANCE_PASSWORD},
        ]),
    )


@pytest.fixture
def clock():
    return FakeClock()


@pytest.fixture
def app(settings, clock):
    return create_app(settings, clock=clock)


@pytest.fixture
def client(app):
    return TestClient(app)


@pytest.fixture
def admin(client):
    assert client.post("/api/auth/login", json={"password": ADMIN_PASSWORD}).status_code == 204
    return client
