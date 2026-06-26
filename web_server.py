import argparse
import os
from typing import Optional

from fastapi import FastAPI, Depends, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from core.config import ConfigManager
from core.ssh_manager import SSHProcessManager


class AppState:
    def __init__(self, token: str, config_path: Optional[str] = None):
        self.token = token
        self.config = ConfigManager(config_path=config_path)
        self.manager = SSHProcessManager(self.config)
        self.manager.start_polling()


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

    return app
