import pytest
from fastapi.testclient import TestClient
from web_server import create_app


@pytest.fixture
def client(tmp_path):
    app = create_app(token="secret", config_path=str(tmp_path / "sessions.json"))
    return TestClient(app)


def test_login_ok(client):
    r = client.post("/api/login", json={"token": "secret"})
    assert r.status_code == 200
    assert r.json()["token"] == "secret"


def test_login_wrong(client):
    r = client.post("/api/login", json={"token": "wrong"})
    assert r.status_code == 401


def test_protected_without_token(client):
    r = client.get("/api/sessions")
    assert r.status_code == 401


def test_list_sessions_empty(client):
    r = client.get("/api/sessions", headers={"Authorization": "Bearer secret"})
    assert r.status_code == 200
    assert r.json() == []
