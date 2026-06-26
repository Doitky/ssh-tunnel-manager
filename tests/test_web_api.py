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


def test_create_session(client):
    r = client.post("/api/sessions", headers={"Authorization": "Bearer secret"},
                    json={"name": "a", "host": "h", "username": "u", "port": 22,
                          "auth_method": "password", "forward_rules": []})
    assert r.status_code == 200
    assert r.json()["name"] == "a"


def test_create_duplicate_port_returns_400(client):
    payload = {"name": "a", "host": "h", "username": "u",
               "forward_rules": [{"direction": "local", "local_port": 8080,
                                  "remote_host": "127.0.0.1", "remote_port": 80}]}
    client.post("/api/sessions", headers={"Authorization": "Bearer secret"}, json=payload)
    payload["name"] = "b"
    r = client.post("/api/sessions", headers={"Authorization": "Bearer secret"}, json=payload)
    assert r.status_code == 400
    assert "8080" in r.json()["detail"]


def test_delete_session(client):
    client.post("/api/sessions", headers={"Authorization": "Bearer secret"},
                json={"name": "a", "host": "h", "username": "u", "forward_rules": []})
    r = client.delete("/api/sessions/a", headers={"Authorization": "Bearer secret"})
    assert r.status_code == 200
    r = client.get("/api/sessions", headers={"Authorization": "Bearer secret"})
    assert r.json() == []
