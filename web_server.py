import argparse
import os
from typing import Optional

from fastapi import FastAPI, Depends, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from core.config import ConfigManager
from core.models import SSHSession
from core.ssh_manager import SSHProcessManager


class AppState:
    def __init__(self, token: str, config_path: Optional[str] = None):
        self.token = token
        self.config = ConfigManager(config_path=config_path)
        self.manager = SSHProcessManager(self.config)
        self.manager.start_polling()
        self.logs: dict[str, list[str]] = {}
        self.manager.subscribe(self._on_event)

    def _on_event(self, event: dict):
        name = event.get("name")
        if not name:
            return
        self.logs.setdefault(name, [])
        if event.get("type") == "log":
            for line in str(event.get("lines", "")).splitlines():
                self.logs[name].append(line)
        elif event.get("type") == "status":
            self.logs[name].append(f"[{event.get('status')}] {event.get('detail', '')}")
        # 限制日志长度
        self.logs[name] = self.logs[name][-500:]


_state: Optional[AppState] = None


def create_app(token: str, config_path: Optional[str] = None) -> FastAPI:
    global _state
    _state = AppState(token=token, config_path=config_path)
    app = FastAPI(title="SSH Tunnel Manager")

    def _require_token(request: Request):
        auth = request.headers.get("authorization", "")
        if auth.startswith("Bearer "):
            tok = auth[len("Bearer "):]
            if tok == _state.token:
                return tok
        raise HTTPException(status_code=401, detail="unauthorized")

    class LoginIn(BaseModel):
        token: str

    @app.post("/api/login")
    def login(body: LoginIn):
        if body.token == _state.token:
            return {"token": body.token}
        raise HTTPException(status_code=401, detail="invalid token")

    @app.get("/api/sessions")
    def list_sessions(_t: str = Depends(_require_token)):
        out = []
        for s in _state.config.list_sessions():
            d = s.to_dict()
            d["status"] = "active" if _state.manager.is_active(s.name) else "idle"
            out.append(d)
        return out

    @app.post("/api/sessions")
    def save_session(body: dict, _t: str = Depends(_require_token)):
        try:
            session = SSHSession.from_dict(body)
        except Exception as e:
            raise HTTPException(status_code=422, detail=f"invalid session: {e}")
        ok, err = _state.config.add_session(session)
        if not ok:
            raise HTTPException(status_code=400, detail=err)
        return session.to_dict()

    @app.delete("/api/sessions/{name}")
    def delete_session(name: str, _t: str = Depends(_require_token)):
        if _state.config.get_session(name) is None:
            raise HTTPException(status_code=404, detail="not found")
        _state.config.remove_session(name)
        return {"ok": True}

    @app.post("/api/sessions/{name}/connect")
    def connect(name: str, _t: str = Depends(_require_token)):
        if _state.config.get_session(name) is None:
            raise HTTPException(status_code=404, detail="not found")
        _state.manager.start_session(name)
        return {"ok": True}

    @app.post("/api/sessions/{name}/disconnect")
    def disconnect(name: str, _t: str = Depends(_require_token)):
        if _state.config.get_session(name) is None:
            raise HTTPException(status_code=404, detail="not found")
        _state.manager.stop_session(name)
        return {"ok": True}

    @app.post("/api/connect-all")
    def connect_all(_t: str = Depends(_require_token)):
        for s in _state.config.list_sessions():
            if s.enabled:
                _state.manager.start_session(s.name)
        return {"ok": True}

    @app.post("/api/disconnect-all")
    def disconnect_all(_t: str = Depends(_require_token)):
        for s in list(_state.manager.active_processes.keys()):
            _state.manager.stop_session(s)
        return {"ok": True}

    @app.get("/api/sessions/{name}/logs")
    def get_logs(name: str, _t: str = Depends(_require_token)):
        if _state.config.get_session(name) is None:
            raise HTTPException(status_code=404, detail="not found")
        return {"lines": "\n".join(_state.logs.get(name, []))}

    return app
