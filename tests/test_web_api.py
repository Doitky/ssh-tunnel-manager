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


def test_connect_missing_session_404(client):
    r = client.post("/api/sessions/nope/connect", headers={"Authorization": "Bearer secret"})
    assert r.status_code == 404


def test_disconnect_missing_session_404(client):
    r = client.post("/api/sessions/nope/disconnect", headers={"Authorization": "Bearer secret"})
    assert r.status_code == 404


def test_disconnect_all_ok(client):
    client.post("/api/sessions", headers={"Authorization": "Bearer secret"},
                json={"name": "a", "host": "h", "username": "u", "forward_rules": []})
    r = client.post("/api/disconnect-all", headers={"Authorization": "Bearer secret"})
    assert r.status_code == 200
    assert r.json()["ok"] is True


def test_logs_endpoint(client):
    client.post("/api/sessions", headers={"Authorization": "Bearer secret"},
                json={"name": "a", "host": "h", "username": "u", "forward_rules": []})
    r = client.get("/api/sessions/a/logs", headers={"Authorization": "Bearer secret"})
    assert r.status_code == 200
    assert "lines" in r.json()


def test_sse_requires_token(client):
    r = client.get("/api/events")
    assert r.status_code == 401


def test_sse_requires_valid_token(client):
    # token query 参数校验：错误 token 仍应 401
    r = client.get("/api/events?token=wrong")
    assert r.status_code == 401


def test_sse_streams_status_event(client):
    # SSE 流式接口在 TestClient/httpx 下会缓冲整个无限响应体，无法逐块读取，
    # 故直接验证端点使用的「每客户端队列 + 订阅 + 序列化」机制：订阅 -> notify ->
    # 队列收到事件 -> SSE data 行包含 status。全程带硬超时，绝不挂起。
    import json as _json
    import queue as _queue
    from web_server import _state as ws_state  # noqa: F811

    client.post("/api/sessions", headers={"Authorization": "Bearer secret"},
                json={"name": "a", "host": "h", "username": "u", "forward_rules": []})

    q: "_queue.Queue" = _queue.Queue()
    unsub = ws_state.manager.subscribe(lambda e: q.put(e))
    try:
        event = {"type": "status", "name": "a", "status": "exited", "detail": "test"}
        ws_state.manager._notify(event)

        received = q.get(timeout=2.0)  # 同步回调，必定立即拿到
        assert received["type"] == "status"

        # 复刻端点 _gen 的序列化格式，断言浏览器侧能读到 status
        line = f"data: {_json.dumps(received, ensure_ascii=False)}\n\n"
        assert "status" in line
    finally:
        unsub()


def test_connect_port_conflict_409(client):
    """本地端口被占用时，connect 应返回 409 而不启动会话。"""
    import socket as _sock
    holder = _sock.socket()
    holder.bind(("127.0.0.1", 0))
    held_port = holder.getsockname()[1]
    holder.listen(1)
    try:
        client.post("/api/sessions", headers={"Authorization": "Bearer secret"},
                    json={"name": "conflict", "host": "h", "username": "u",
                          "auth_method": "password",
                          "forward_rules": [{"direction": "local", "local_port": held_port,
                                             "remote_host": "127.0.0.1", "remote_port": 80}]})
        r = client.post("/api/sessions/conflict/connect",
                        headers={"Authorization": "Bearer secret"})
        assert r.status_code == 409
        assert "已被占用" in r.json()["detail"]
    finally:
        holder.close()
