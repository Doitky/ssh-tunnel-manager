# B/S 架构改造 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在保留桌面 GUI 的前提下，新增 FastAPI + 原生前端的 B/S 架构，通过浏览器管理 SSH 端口转发，且 GUI 与 Web 共享同一份会话配置。

**Architecture:** 把现有单文件的核心类（PortForwardRule / SSHSession / ConfigManager / SSHProcessManager）抽取为 `core/` 包，不依赖 tkinter；GUI 与 Web 均复用此核心层。Web 层用 FastAPI 提供 REST + SSE API，前端为原生 HTML/JS 单页。SSHProcessManager 的单轮询回调升级为多订阅者事件分发，对 GUI 透明。

**Tech Stack:** Python 3.12（venv）、FastAPI、uvicorn[standard]、httpx（测试）、pytest、原生 HTML/CSS/JS、SSE（EventSource）。

## Global Constraints

- Python 解释器：`/usr/local/bin/python3.12`（项目 AGENTS.md 要求）。
- 依赖通过项目根 `.venv` 管理，不污染系统 Python；运行测试用 `.venv/bin/pytest`，运行服务用 `.venv/bin/python`。
- 网络超时时设置代理：`export https_proxy=http://127.0.0.1:7890 && export http_proxy=http://127.0.0.1:7890`。
- 核心层 `core/` 不得 `import tkinter`（保证 headless 可用）。
- 会话配置路径固定：`~/.ssh_tunnel_manager/sessions.json`，GUI 与 Web 共享，不做文件锁。
- 改动遵循外科手术原则：只动拆分与新功能相关代码，不重构无关部分。
- 中文回答；修改前已有本计划作为说明。
- `.venv/` 已加入 `.gitignore`（Task 1 处理）。

---

## File Structure

- `core/__init__.py` — 包导出，空或仅导出主要类。
- `core/models.py` — `PortForwardRule`、`SSHSession` dataclass（从原文件搬出）。
- `core/config.py` — `ConfigManager`（从原文件搬出）。
- `core/ssh_manager.py` — `SSHProcessManager`（搬出 + 事件分发改造）。
- `ssh_tunnel_manager.py` — GUI 入口，改为 `from core... import`（行为不变）。
- `web_server.py` — 新增 FastAPI 应用 + 启动入口 + 鉴权 + SSE。
- `web/index.html`、`web/app.js`、`web/style.css` — 新增前端单页。
- `requirements-web.txt` — 新增 Web 依赖清单。
- `.gitignore` — 追加 `.venv/`。
- `tests/test_core_models.py`、`tests/test_core_config.py`、`tests/test_core_ssh_manager.py`、`tests/test_web_api.py` — 新增测试。
- `README_zh.md` — 追加 Web 运行说明（Task 9）。

---

### Task 1: 项目脚手架与依赖

**Files:**
- Create: `requirements-web.txt`
- Create: `tests/__init__.py`
- Modify: `.gitignore`（追加 `.venv/`）

**Interfaces:**
- Produces: `.venv` 可用环境；`requirements-web.txt` 记录 Web 依赖；`.gitignore` 忽略 `.venv/`、`tests/__pycache__/`。

- [ ] **Step 1: 写 `requirements-web.txt`**

创建文件，内容：

```
fastapi>=0.110
uvicorn[standard]>=0.29
httpx>=0.27
pytest>=8.0
```

- [ ] **Step 2: 追加 `.gitignore` 忽略项**

在 `.gitignore` 末尾追加：

```
# Web dev
.venv/
```

- [ ] **Step 3: 创建空 `tests/__init__.py`**

```bash
mkdir -p tests && touch tests/__init__.py
```

- [ ] **Step 4: 验证 venv 与依赖可用**

运行（如已建 `.venv` 则跳过创建）：

```bash
.venv/bin/python -c "import fastapi, uvicorn, httpx, pytest; print('deps ok')"
```

Expected: 输出 `deps ok`。

- [ ] **Step 5: Commit**

```bash
git add requirements-web.txt .gitignore tests/__init__.py
git commit -m "chore: web 依赖与测试脚手架"
```

---

### Task 2: 抽取 core/models.py

**Files:**
- Create: `core/__init__.py`
- Create: `core/models.py`
- Test: `tests/test_core_models.py`

**Interfaces:**
- Produces: `core.models.PortForwardRule`、`core.models.SSHSession`，含 `to_dict()` / `from_dict()`。
- Consumes: 无。

- [ ] **Step 1: 写失败测试 `tests/test_core_models.py`**

```python
from core.models import PortForwardRule, SSHSession


def test_port_forward_rule_to_dict():
    rule = PortForwardRule(direction="local", local_port=8080,
                           remote_host="127.0.0.1", remote_port=80)
    d = rule.to_dict()
    assert d == {
        "direction": "local", "local_port": 8080,
        "remote_host": "127.0.0.1", "remote_port": 80,
        "description": "",
    }


def test_port_forward_rule_from_dict_ignores_extra():
    rule = PortForwardRule.from_dict({
        "direction": "remote", "local_port": 9090,
        "remote_host": "db", "remote_port": 5432,
        "description": "db", "extra_field": "ignore me",
    })
    assert rule.direction == "remote" and rule.local_port == 9090


def test_ssh_session_roundtrip():
    s = SSHSession(name="t", host="h", username="u",
                   forward_rules=[PortForwardRule(direction="dynamic", local_port=1080)])
    d = s.to_dict()
    assert d["forward_rules"][0]["direction"] == "dynamic"
    s2 = SSHSession.from_dict(d)
    assert s2.name == "t" and isinstance(s2.forward_rules[0], PortForwardRule)
    assert s2.forward_rules[0].local_port == 1080


def test_ssh_session_defaults():
    s = SSHSession(name="n")
    assert s.port == 22 and s.auth_method == "password"
    assert s.keepalive_enabled is True and s.keepalive_interval == 30
    assert s.forward_rules == [] and s.enabled is True
    assert s.created_at and s.updated_at
```

- [ ] **Step 2: 运行测试验证失败**

Run: `.venv/bin/pytest tests/test_core_models.py -v`
Expected: FAIL（`ModuleNotFoundError: No module named 'core'`）

- [ ] **Step 3: 创建 `core/__init__.py`**

```python
# core package: shared logic for GUI and Web, no tkinter dependency.
```

- [ ] **Step 4: 创建 `core/models.py`**

从 `ssh_tunnel_manager.py` 原样搬出第 24-75 行两个 dataclass（`PortForwardRule`、`SSHSession`，含 `@dataclass`、`__post_init__`、`to_dict`、`from_dict`），保留所有导入（`dataclass, field, asdict`、`datetime`）。不改动逻辑。

```python
from dataclasses import dataclass, field, asdict
from datetime import datetime


@dataclass
class PortForwardRule:
    """Single port forwarding rule."""
    direction: str = "local"       # local | remote | dynamic
    local_port: int = 0
    remote_host: str = "127.0.0.1"
    remote_port: int = 0
    description: str = ""

    def to_dict(self):
        return asdict(self)

    @classmethod
    def from_dict(cls, d):
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})


@dataclass
class SSHSession:
    """An SSH session configuration."""
    name: str = ""
    host: str = ""
    port: int = 22
    username: str = ""
    auth_method: str = "password"  # password | key
    key_path: str = ""
    password: str = ""
    remote_cmd: str = ""
    keepalive_enabled: bool = True
    keepalive_interval: int = 30   # seconds
    forward_rules: list = field(default_factory=list)
    enabled: bool = True
    created_at: str = ""
    updated_at: str = ""

    def __post_init__(self):
        if not self.created_at:
            self.created_at = datetime.now().isoformat()
        self.updated_at = datetime.now().isoformat()

    def to_dict(self):
        d = asdict(self)
        d["forward_rules"] = [r.to_dict() for r in self.forward_rules]
        return d

    @classmethod
    def from_dict(cls, d):
        session = cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})
        session.forward_rules = [PortForwardRule.from_dict(r) for r in d.get("forward_rules", [])]
        return session
```

- [ ] **Step 5: 运行测试验证通过**

Run: `.venv/bin/pytest tests/test_core_models.py -v`
Expected: 4 passed

- [ ] **Step 6: Commit**

```bash
git add core/__init__.py core/models.py tests/test_core_models.py
git commit -m "feat(core): 抽取 models（PortForwardRule/SSHSession）"
```

---

### Task 3: 抽取 core/config.py

**Files:**
- Create: `core/config.py`
- Test: `tests/test_core_config.py`

**Interfaces:**
- Produces: `core.config.ConfigManager(config_path=None)`，方法 `add_session(session)->(bool, Optional[str])`、`remove_session(name)`、`get_session(name)->Optional[SSHSession]`、`list_sessions()->list[SSHSession]`、`save()`。
- Consumes: `core.models.SSHSession`、`core.models.PortForwardRule`。

- [ ] **Step 1: 写失败测试 `tests/test_core_config.py`**

```python
import os
import pytest
from core.config import ConfigManager
from core.models import SSHSession, PortForwardRule


@pytest.fixture
def cfg(tmp_path):
    return ConfigManager(config_path=str(tmp_path / "sessions.json"))


def test_add_and_list(cfg):
    ok, err = cfg.add_session(SSHSession(name="a", host="h", username="u"))
    assert ok and err is None
    names = [s.name for s in cfg.list_sessions()]
    assert names == ["a"]


def test_duplicate_local_port_conflict(cfg):
    cfg.add_session(SSHSession(name="a", forward_rules=[
        PortForwardRule(direction="local", local_port=8080)]))
    ok, err = cfg.add_session(SSHSession(name="b", forward_rules=[
        PortForwardRule(direction="local", local_port=8080)]))
    assert ok is False
    assert "8080" in err and "a" in err


def test_update_same_session_no_conflict(cfg):
    cfg.add_session(SSHSession(name="a", forward_rules=[
        PortForwardRule(direction="local", local_port=8080)]))
    ok, err = cfg.add_session(SSHSession(name="a", host="h2", forward_rules=[
        PortForwardRule(direction="local", local_port=8080)]))
    assert ok and err is None
    assert cfg.get_session("a").host == "h2"


def test_remove_and_persist(cfg, tmp_path):
    cfg.add_session(SSHSession(name="a", host="h", username="u"))
    cfg.remove_session("a")
    assert cfg.get_session("a") is None
    # reload from disk
    cfg2 = ConfigManager(config_path=cfg.config_path)
    assert cfg2.get_session("a") is None
```

- [ ] **Step 2: 运行测试验证失败**

Run: `.venv/bin/pytest tests/test_core_config.py -v`
Expected: FAIL（`ModuleNotFoundError: No module named 'core.config'`）

- [ ] **Step 3: 创建 `core/config.py`**

从 `ssh_tunnel_manager.py` 原样搬出第 77-147 行 `ConfigManager`，import 改为从 `core.models` 引入。逻辑不变。

```python
import json
import os
from typing import Optional

from core.models import SSHSession


class ConfigManager:
    """Manages persistent storage of sessions."""

    def __init__(self, config_path: Optional[str] = None):
        if config_path is None:
            home = os.path.expanduser("~")
            config_dir = os.path.join(home, ".ssh_tunnel_manager")
            os.makedirs(config_dir, exist_ok=True)
            config_path = os.path.join(config_dir, "sessions.json")
        self.config_path = config_path
        self.sessions: dict[str, SSHSession] = {}
        self._load()

    def _load(self):
        if os.path.exists(self.config_path):
            try:
                with open(self.config_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                for name, d in data.items():
                    self.sessions[name] = SSHSession.from_dict(d)
            except (json.JSONDecodeError, KeyError):
                self.sessions = {}

    def save(self):
        data = {name: s.to_dict() for name, s in self.sessions.items()}
        with open(self.config_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)

    def _collect_local_ports(self, session: SSHSession) -> set[int]:
        """Collect all local_port values from a session's forward rules."""
        ports = set()
        for rule in getattr(session, "forward_rules", []):
            lp = getattr(rule, "local_port", 0)
            if lp:
                ports.add(lp)
        return ports

    def add_session(self, session: SSHSession) -> tuple[bool, Optional[str]]:
        """Add or update a session.

        Returns (success, error_message).
        On duplicate local_port, returns (False, "local_port X is used by session Y").
        """
        new_ports = self._collect_local_ports(session)
        for existing in self.sessions.values():
            if existing.name == session.name:
                continue
            existing_ports = self._collect_local_ports(existing)
            overlap = new_ports & existing_ports
            if overlap:
                port = sorted(overlap)[0]
                return (False, f"Local port {port} is already used by session '{existing.name}'. Please modify it.")

        if session.name in self.sessions:
            self.sessions[session.name] = session
        else:
            self.sessions[session.name] = session
        session.updated_at = datetime.now().isoformat()
        self.save()
        return (True, None)

    def remove_session(self, name: str):
        self.sessions.pop(name, None)
        self.save()

    def get_session(self, name: str) -> Optional[SSHSession]:
        return self.sessions.get(name)

    def list_sessions(self) -> list[SSHSession]:
        return sorted(self.sessions.values(), key=lambda s: s.name)
```

注意：`add_session` 用到 `datetime`，文件头需补 `from datetime import datetime`。

- [ ] **Step 4: 运行测试验证通过**

Run: `.venv/bin/pytest tests/test_core_config.py -v`
Expected: 4 passed

- [ ] **Step 5: Commit**

```bash
git add core/config.py tests/test_core_config.py
git commit -m "feat(core): 抽取 ConfigManager"
```

---

### Task 4: 抽取 core/ssh_manager.py 并升级为事件分发

**Files:**
- Create: `core/ssh_manager.py`
- Test: `tests/test_core_ssh_manager.py`

**Interfaces:**
- Consumes: `core.config.ConfigManager`、`core.models.SSHSession`、`core.models.PortForwardRule`。
- Produces: `core.ssh_manager.SSHProcessManager`，方法：
  - `start_session(session_name, callback=None)`
  - `stop_session(session_name) -> bool`
  - `start_polling(callback=None, interval=2.0)`（保留，内部订阅事件）
  - `stop_polling()`
  - `subscribe(callback) -> unsubscribe_fn`（新增，多订阅者事件分发）
  - 属性 `active_processes: dict`
  - 新增 `is_active(name) -> bool`

事件 dict 结构：
- `{"type": "status", "name": str, "status": "active"|"exited"|"error", "detail": str}`
- `{"type": "log", "name": str, "lines": str}`

- [ ] **Step 1: 写失败测试 `tests/test_core_ssh_manager.py`**

```python
import pytest
from core.ssh_manager import SSHProcessManager
from core.config import ConfigManager
from core.models import SSHSession


@pytest.fixture
def mgr(tmp_path):
    cfg = ConfigManager(config_path=str(tmp_path / "sessions.json"))
    return SSHProcessManager(cfg)


def test_subscribe_and_unsubscribe(mgr):
    events = []
    unsub = mgr.subscribe(lambda e: events.append(e))
    mgr._notify({"type": "status", "name": "x", "status": "active", "detail": "Running"})
    assert events and events[0]["status"] == "active"
    unsub()
    mgr._notify({"type": "status", "name": "x", "status": "exited", "detail": "done"})
    assert len(events) == 1  # 第二次不再收到


def test_is_active_false_when_not_running(mgr):
    assert mgr.is_active("nope") is False


def test_build_ssh_command_local_forward(mgr, tmp_path):
    from core.models import SSHSession, PortForwardRule
    s = SSHSession(name="t", host="h", username="u", auth_method="key",
                   key_path=str(tmp_path / "k"),
                   forward_rules=[PortForwardRule(direction="local", local_port=8080,
                                                  remote_host="127.0.0.1", remote_port=80)])
    cmd = mgr._build_ssh_command(s)
    assert "-L" in cmd
    assert "8080:127.0.0.1:80" in " ".join(cmd)
```

- [ ] **Step 2: 运行测试验证失败**

Run: `.venv/bin/pytest tests/test_core_ssh_manager.py -v`
Expected: FAIL（`ModuleNotFoundError: No module named 'core.ssh_manager'`）

- [ ] **Step 3: 创建 `core/ssh_manager.py`**

搬出原文件第 151-378 行 `SSHProcessManager`，import 改为 `from core.config import ConfigManager` / `from core.models import SSHSession`。改造点：
1. `__init__` 增加 `self._subscribers: list = []`。
2. 新增方法：

```python
    def subscribe(self, callback) -> "callable":
        """Register a subscriber for status/log events. Returns unsubscribe fn."""
        self._subscribers.append(callback)

        def _unsubscribe():
            if callback in self._subscribers:
                self._subscribers.remove(callback)

        return _unsubscribe

    def _notify(self, event: dict):
        for cb in list(self._subscribers):
            try:
                cb(event)
            except Exception:
                pass
```

3. 新增方法：

```python
    def is_active(self, session_name: str) -> bool:
        info = self.active_processes.get(session_name)
        if info is None:
            return False
        proc = info.get("proc")
        return proc is not None and proc.poll() is None
```

4. 在 `_run` 内的日志处，除调用 `callback` 外，同时 `self._notify({"type": "log", "name": session_name, "lines": "\n".join(log_lines)})`。
5. 在 `start_polling` 的 `_poll_loop` 中，状态变化时除调用 `self._poll_callback` 外，同时 `self._notify({"type": "status", "name": name, "status": <status>, "detail": detail})`。

保留原 `start_polling(callback=...)` 签名与 `_poll_callback` 行为（GUI 仍可传单回调，等价订阅）。其余 `_find_sshpass` / `_build_ssh_command` / `_start_keepalive` / `_stop_keepalive` 原样保留。

- [ ] **Step 4: 运行测试验证通过**

Run: `.venv/bin/pytest tests/test_core_ssh_manager.py -v`
Expected: 3 passed

- [ ] **Step 5: Commit**

```bash
git add core/ssh_manager.py tests/test_core_ssh_manager.py
git commit -m "feat(core): 抽取 SSHProcessManager 并升级事件分发"
```

---

### Task 5: GUI 改为复用 core（行为不变）

**Files:**
- Modify: `ssh_tunnel_manager.py`（顶部 import 区 + 类定义删除，改为 import core）

**Interfaces:**
- Consumes: `core.models.*`、`core.config.ConfigManager`、`core.ssh_manager.SSHProcessManager`。
- Produces: GUI 行为与改造前完全一致。

- [ ] **Step 1: 替换 import 与移除内联类定义**

在 `ssh_tunnel_manager.py` 顶部，将原 `from dataclasses import ...`、`from datetime import datetime` 保留（GUI 其他处可能用到 datetime），删除内联的 `PortForwardRule`、`SSHSession`、`ConfigManager`、`SSHProcessManager` 四个类定义（原第 24-378 行），改为：

```python
from core.models import PortForwardRule, SSHSession
from core.config import ConfigManager
from core.ssh_manager import SSHProcessManager
```

`SSHTunnelManagerApp._start_polling` 中调用 `start_polling(callback)` 保持不变（事件分发对 GUI 透明）。

- [ ] **Step 2: 用 import 自检确认无语法/引用错误**

Run: `.venv/bin/python -c "import ssh_tunnel_manager; print('gui import ok')"`
Expected: 输出 `gui import ok`（注意：import tkinter 在有显示环境可用；若 CI 无显示可跳过此步，改为 `py_compile`）。

若环境无显示，改用：

Run: `.venv/bin/python -m py_compile ssh_tunnel_manager.py && echo compile-ok`
Expected: `compile-ok`

- [ ] **Step 3: 确认 core 无 tkinter 依赖**

Run: `.venv/bin/python -c "import core.models, core.config, core.ssh_manager; print('core no-tk ok')"`
Expected: `core no-tk ok`

- [ ] **Step 4: 运行全部核心测试**

Run: `.venv/bin/pytest tests/ -v`
Expected: 之前所有测试仍 passed

- [ ] **Step 5: Commit**

```bash
git add ssh_tunnel_manager.py
git commit -m "refactor(gui): 改为复用 core 包（行为不变）"
```

---

### Task 6: FastAPI 应用骨架与鉴权

**Files:**
- Create: `web_server.py`
- Test: `tests/test_web_api.py`

**Interfaces:**
- Produces: `web_server.py` 内 `app: FastAPI`、`create_app(token, config_path=None)` 工厂、`_require_token` 依赖。单例 `state` 持有 `ConfigManager` + `SSHProcessManager`。
- Consumes: `core.config.ConfigManager`、`core.ssh_manager.SSHProcessManager`。

- [ ] **Step 1: 写失败测试 `tests/test_web_api.py`（鉴权与 sessions 列表）**

```python
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
```

- [ ] **Step 2: 运行测试验证失败**

Run: `.venv/bin/pytest tests/test_web_api.py -v`
Expected: FAIL（`ModuleNotFoundError: No module named 'web_server'`）

- [ ] **Step 3: 创建 `web_server.py`（骨架 + login + sessions 列表）**

```python
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
```

- [ ] **Step 4: 运行测试验证通过**

Run: `.venv/bin/pytest tests/test_web_api.py -v`
Expected: 4 passed

- [ ] **Step 5: Commit**

```bash
git add web_server.py tests/test_web_api.py
git commit -m "feat(web): FastAPI 骨架与 token 鉴权"
```

---

### Task 7: 会话增删改 API

**Files:**
- Modify: `web_server.py`（追加 session CRUD 路由）
- Modify: `tests/test_web_api.py`（追加 CRUD 测试）

**Interfaces:**
- Produces: `POST /api/sessions`、`DELETE /api/sessions/{name}`。

- [ ] **Step 1: 追加失败测试**

在 `tests/test_web_api.py` 末尾追加：

```python
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
```

- [ ] **Step 2: 运行测试验证失败**

Run: `.venv/bin/pytest tests/test_web_api.py -v`
Expected: 3 个新测试 FAIL（404/405）

- [ ] **Step 3: 在 `create_app` 内 `list_sessions` 路由后追加 CRUD 路由**

```python
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
```

并确保文件顶部 import 增加 `from core.models import SSHSession`。

- [ ] **Step 4: 运行测试验证通过**

Run: `.venv/bin/pytest tests/test_web_api.py -v`
Expected: 7 passed

- [ ] **Step 5: Commit**

```bash
git add web_server.py tests/test_web_api.py
git commit -m "feat(web): 会话增删改 API"
```

---

### Task 8: 连接控制 API（connect/disconnect/connect-all/disconnect-all/logs）

**Files:**
- Modify: `web_server.py`
- Modify: `tests/test_web_api.py`

**Interfaces:**
- Produces: `POST /api/sessions/{name}/connect`、`POST /api/sessions/{name}/disconnect`、`POST /api/connect-all`、`POST /api/disconnect-all`、`GET /api/sessions/{name}/logs`。

- [ ] **Step 1: 追加失败测试**

在 `tests/test_web_api.py` 末尾追加（断开对不存在的会话返回 404；连接不存在的会话 404；logs 接口可用）：

```python
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
```

- [ ] **Step 2: 运行测试验证失败**

Run: `.venv/bin/pytest tests/test_web_api.py -v`
Expected: 4 个新测试 FAIL（404/405）

- [ ] **Step 3: 在 `AppState` 增加日志缓冲并追加路由**

在 `AppState.__init__` 末尾增加：

```python
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
```

在 `create_app` 内追加路由：

```python
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
```

- [ ] **Step 4: 运行测试验证通过**

Run: `.venv/bin/pytest tests/test_web_api.py -v`
Expected: 11 passed

- [ ] **Step 5: Commit**

```bash
git add web_server.py tests/test_web_api.py
git commit -m "feat(web): 连接控制与日志 API"
```

---

### Task 9: SSE 事件流端点

**Files:**
- Modify: `web_server.py`
- Modify: `tests/test_web_api.py`

**Interfaces:**
- Produces: `GET /api/events?token=<token>`，SSE 流，推送 `status` / `log` 事件。
- 注意：EventSource 不支持自定义 header，token 经 query 参数校验。

- [ ] **Step 1: 追加失败测试（用 httpx 流式读取 SSE）**

在 `tests/test_web_api.py` 末尾追加：

```python
import threading
import time as _time


def test_sse_requires_token(client):
    r = client.get("/api/events")
    assert r.status_code == 401


def test_sse_streams_status_event(client):
    client.post("/api/sessions", headers={"Authorization": "Bearer secret"},
                json={"name": "a", "host": "h", "username": "u", "forward_rules": []})
    with client.stream("GET", "/api/events?token=secret") as resp:
        assert resp.status_code == 200
        # 触发一个内部事件
        _state_event = {"type": "status", "name": "a", "status": "exited", "detail": "test"}
        # 通过 manager 直接 notify
        from web_server import _state as ws_state  # noqa: F811
        ws_state.manager._notify(_state_event)
        # 读取首条事件
        received = b""
        for chunk in resp.iter_raw():
            received += chunk
            if b"data:" in received:
                break
    assert b"status" in received
```

- [ ] **Step 2: 运行测试验证失败**

Run: `.venv/bin/pytest tests/test_web_api.py::test_sse_requires_token tests/test_web_api.py::test_sse_streams_status_event -v`
Expected: FAIL

- [ ] **Step 3: 在 `create_app` 内追加 SSE 端点**

```python
    import asyncio
    from fastapi.responses import StreamingResponse

    @app.get("/api/events")
    def events(token: Optional[str] = None):
        if token != _state.token:
            raise HTTPException(status_code=401, detail="unauthorized")
        import queue
        q: "queue.Queue" = queue.Queue()
        unsub = _state.manager.subscribe(lambda e: q.put(e))

        async def _gen():
            try:
                while True:
                    try:
                        ev = q.get(timeout=1.0)
                        yield f"event: {ev.get('type', 'message')}\n"
                        import json as _json
                        yield f"data: {_json.dumps(ev, ensure_ascii=False)}\n\n"
                    except Exception:
                        # 超时空行保活
                        yield ": keep-alive\n\n"
            finally:
                unsub()

        return StreamingResponse(_gen(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache",
                                          "X-Accel-Buffering": "no"})
```

- [ ] **Step 4: 运行测试验证通过**

Run: `.venv/bin/pytest tests/test_web_api.py -v`
Expected: 13 passed

- [ ] **Step 5: Commit**

```bash
git add web_server.py tests/test_web_api.py
git commit -m "feat(web): SSE 事件流端点"
```

---

### Task 10: 静态前端单页（HTML/CSS/JS）

**Files:**
- Create: `web/index.html`
- Create: `web/app.js`
- Create: `web/style.css`
- Modify: `web_server.py`（挂载静态资源与根路径）

**Interfaces:**
- Produces: 浏览器访问 `/` 返回 `web/index.html`；`/web/` 提供静态 JS/CSS。

- [ ] **Step 1: 在 `create_app` 内挂载静态与根路径**

在 `web_server.py` `create_app` 末尾（return app 前）追加：

```python
    import pathlib
    _web_dir = pathlib.Path(__file__).parent / "web"

    @app.get("/")
    def index():
        return FileResponse(str(_web_dir / "index.html"))

    app.mount("/web", StaticFiles(directory=str(_web_dir)), name="web")
```

- [ ] **Step 2: 创建 `web/style.css`**

简洁暗色面板样式：左侧会话列表 + 右侧详情；状态点绿（active）/灰（idle）。

```css
* { box-sizing: border-box; }
body { margin: 0; font-family: -apple-system, "PingFang SC", sans-serif;
       background: #1e1e1e; color: #ddd; display: flex; height: 100vh; }
#login { margin: auto; padding: 2rem; background: #2a2a2a; border-radius: 8px; }
#login input { padding: .5rem; margin: .3rem 0; width: 100%; }
#login button { padding: .5rem 1rem; width: 100%; cursor: pointer; }
#app { display: none; width: 100%; }
#sidebar { width: 280px; background: #252526; border-right: 1px solid #333; overflow-y: auto; }
#sidebar h2 { padding: 1rem; margin: 0; font-size: 1rem; }
.session-item { padding: .8rem 1rem; cursor: pointer; border-bottom: 1px solid #333; display: flex; justify-content: space-between; align-items: center; }
.session-item:hover { background: #2d2d2d; }
.session-item.active { background: #094771; }
.dot { width: 10px; height: 10px; border-radius: 50%; background: #666; }
.dot.active { background: #4ec9b0; }
#detail { flex: 1; padding: 1rem; overflow-y: auto; }
button.act { padding: .4rem .8rem; margin-right: .5rem; cursor: pointer; }
table { width: 100%; border-collapse: collapse; margin: .5rem 0; }
th, td { padding: .4rem; border: 1px solid #444; text-align: left; }
#log { background: #000; color: #0f0; padding: .8rem; height: 240px; overflow-y: auto;
       font-family: monospace; font-size: .85rem; border-radius: 4px; white-space: pre-wrap; }
.toolbar { margin-bottom: 1rem; }
input, select { background: #333; color: #ddd; border: 1px solid #555; padding: .3rem; }
```

- [ ] **Step 3: 创建 `web/index.html`**

```html
<!DOCTYPE html>
<html lang="zh">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>SSH Tunnel Manager</title>
<link rel="stylesheet" href="/web/style.css">
</head>
<body>
<div id="login">
  <h2>SSH Tunnel Manager</h2>
  <input id="tokenInput" type="password" placeholder="访问口令">
  <button id="loginBtn">登录</button>
  <p id="loginMsg"></p>
</div>
<div id="app">
  <div id="sidebar">
    <h2>会话列表 <button class="act" id="newBtn">+ 新建</button></h2>
    <div id="sessionList"></div>
  </div>
  <div id="detail">
    <div id="editor" style="display:none">
      <h3 id="editorTitle">新建会话</h3>
      <label>名称<input id="f_name"></label><br>
      <label>主机<input id="f_host"></label>
      <label>端口<input id="f_port" type="number" value="22"></label><br>
      <label>用户名<input id="f_username"></label>
      <label>认证<select id="f_auth"><option value="password">密码</option><option value="key">密钥</option></select></label><br>
      <div id="pwdRow"><label>密码<input id="f_password" type="password"></label></div>
      <div id="keyRow" style="display:none"><label>密钥路径<input id="f_keypath"></label></div>
      <label>心跳间隔(秒)<input id="f_keepalive" type="number" value="30"></label>
      <label><input id="f_keepalive_on" type="checkbox" checked>启用心跳</label><br>
      <h4>端口转发规则</h4>
      <table id="rulesTable"><tr><th>方向</th><th>本地端口</th><th>远程主机</th><th>远程端口</th><th>操作</th></tr></table>
      <button class="act" id="addRuleBtn">+ 添加规则</button>
      <div class="toolbar" style="margin-top:1rem">
        <button class="act" id="saveBtn">保存</button>
        <button class="act" id="cancelBtn">取消</button>
      </div>
    </div>
    <div id="view">
      <div class="toolbar">
        <button class="act" id="connectBtn">连接</button>
        <button class="act" id="disconnectBtn">断开</button>
        <button class="act" id="editBtn">编辑</button>
        <button class="act" id="deleteBtn">删除</button>
        <button class="act" id="connectAllBtn">全部连接</button>
        <button class="act" id="disconnectAllBtn">全部断开</button>
      </div>
      <pre id="log"></pre>
    </div>
  </div>
</div>
<script src="/web/app.js"></script>
</body>
</html>
```

- [ ] **Step 4: 创建 `web/app.js`**

完整前端逻辑：登录、CRUD、规则编辑、启停、SSE 实时刷新。

```javascript
let TOKEN = localStorage.getItem("stm_token") || "";
let current = null;
let editingRules = [];
let evtSource = null;

const $ = (id) => document.getElementById(id);
const authHeaders = () => ({ "Authorization": "Bearer " + TOKEN, "Content-Type": "application/json" });

function show(el) { el.style.display = "block"; }
function hide(el) { el.style.display = "none"; }

async function api(method, path, body) {
  const opt = { method, headers: authHeaders() };
  if (body !== undefined) opt.body = JSON.stringify(body);
  const r = await fetch(path, opt);
  if (r.status === 401) { logout(); throw new Error("unauthorized"); }
  if (!r.ok) { const t = await r.text(); throw new Error(t); }
  return r.json();
}

function logout() { localStorage.removeItem("stm_token"); location.reload(); }

$("loginBtn").onclick = async () => {
  const t = $("tokenInput").value.trim();
  if (!t) return;
  try {
    const r = await fetch("/api/login", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ token: t }) });
    if (!r.ok) { $("loginMsg").textContent = "口令错误"; return; }
    TOKEN = t; localStorage.setItem("stm_token", t);
    enterApp();
  } catch (e) { $("loginMsg").textContent = String(e); }
};

function enterApp() { hide($("login")); show($("app")); loadSessions(); openSSE(); }

async function loadSessions() {
  const list = await api("GET", "/api/sessions");
  const box = $("sessionList"); box.innerHTML = "";
  list.forEach((s) => {
    const div = document.createElement("div");
    div.className = "session-item" + (current === s.name ? " active" : "");
    div.innerHTML = `<span>${s.name}</span><span class="dot ${s.status === "active" ? "active" : ""}" title="${s.status}"></span>`;
    div.onclick = () => selectSession(s.name);
    box.appendChild(div);
  });
  if (current) { showView(); } else { hide($("view")); hide($("editor")); }
}

function selectSession(name) {
  current = name; loadSessions();
  show($("view"));
  loadLog();
}

async function loadLog() {
  if (!current) return;
  try { const r = await api("GET", `/api/sessions/${current}/logs`); $("log").textContent = r.lines || "(无日志)"; }
  catch (e) { $("log").textContent = String(e); }
}

function openSSE() {
  if (evtSource) evtSource.close();
  evtSource = new EventSource(`/api/events?token=${encodeURIComponent(TOKEN)}`);
  evtSource.addEventListener("status", (e) => {
    const ev = JSON.parse(e.data);
    if (ev.name === current) loadLog();
    loadSessions();
  });
  evtSource.addEventListener("log", (e) => {
    const ev = JSON.parse(e.data);
    if (ev.name === current) loadLog();
  });
}

function showView() { show($("view")); }
function showEditor(session) {
  hide($("view")); show($("editor"));
  $("editorTitle").textContent = session ? "编辑会话" : "新建会话";
  $("f_name").value = session ? session.name : "";
  $("f_host").value = session ? session.host : "";
  $("f_port").value = session ? session.port : 22;
  $("f_username").value = session ? session.username : "";
  $("f_auth").value = session ? session.auth_method : "password";
  $("f_password").value = session ? session.password : "";
  $("f_keypath").value = session ? session.key_path : "";
  $("f_keepalive").value = session ? session.keepalive_interval : 30;
  $("f_keepalive_on").checked = session ? session.keepalive_enabled : true;
  toggleAuth();
  editingRules = session ? session.forward_rules.map((r) => ({ ...r })) : [];
  renderRules();
}
function toggleAuth() {
  const isPwd = $("f_auth").value === "password";
  $("pwdRow").style.display = isPwd ? "block" : "none";
  $("keyRow").style.display = isPwd ? "none" : "block";
}
$("f_auth").onchange = toggleAuth;

function renderRules() {
  const tbl = $("rulesTable");
  tbl.innerHTML = "<tr><th>方向</th><th>本地端口</th><th>远程主机</th><th>远程端口</th><th>操作</th></tr>";
  editingRules.forEach((r, i) => {
    const tr = document.createElement("tr");
    tr.innerHTML = `<td><select><option ${r.direction === "local" ? "selected" : ""}>local</option><option ${r.direction === "remote" ? "selected" : ""}>remote</option><option ${r.direction === "dynamic" ? "selected" : ""}>dynamic</option></select></td>
      <td><input type="number" value="${r.local_port || ""}"></td>
      <td><input value="${r.remote_host || "127.0.0.1"}"></td>
      <td><input type="number" value="${r.remote_port || ""}"></td>
      <td><button>删除</button></td>`;
    const sel = tr.querySelector("select"); sel.onchange = () => { r.direction = sel.value; };
    const inputs = tr.querySelectorAll("input");
    inputs[0].oninput = () => r.local_port = parseInt(inputs[0].value) || 0;
    inputs[1].oninput = () => r.remote_host = inputs[1].value;
    inputs[2].oninput = () => r.remote_port = parseInt(inputs[2].value) || 0;
    tr.querySelector("button").onclick = () => { editingRules.splice(i, 1); renderRules(); };
    tbl.appendChild(tr);
  });
}
$("addRuleBtn").onclick = () => { editingRules.push({ direction: "local", local_port: 0, remote_host: "127.0.0.1", remote_port: 0 }); renderRules(); };

$("saveBtn").onclick = async () => {
  const body = {
    name: $("f_name").value.trim(), host: $("f_host").value.trim(),
    port: parseInt($("f_port").value) || 22, username: $("f_username").value.trim(),
    auth_method: $("f_auth").value, password: $("f_password").value,
    key_path: $("f_keypath").value, keepalive_enabled: $("f_keepalive_on").checked,
    keepalive_interval: parseInt($("f_keepalive").value) || 30,
    forward_rules: editingRules, enabled: true,
  };
  try { await api("POST", "/api/sessions", body); current = body.name; hide($("editor")); show($("view")); loadSessions(); }
  catch (e) { alert("保存失败: " + e.message); }
};
$("cancelBtn").onclick = () => { hide($("editor")); if (current) show($("view")); };
$("newBtn").onclick = () => { showEditor(null); };
$("editBtn").onclick = async () => {
  const list = await api("GET", "/api/sessions");
  const s = list.find((x) => x.name === current);
  if (s) showEditor(s);
};
$("deleteBtn").onclick = async () => {
  if (!current || !confirm(`删除会话 ${current}?`)) return;
  await api("DELETE", `/api/sessions/${current}`); current = null; hide($("view")); loadSessions();
};
$("connectBtn").onclick = async () => { if (current) { await api("POST", `/api/sessions/${current}/connect`); loadLog(); } };
$("disconnectBtn").onclick = async () => { if (current) await api("POST", `/api/sessions/${current}/disconnect`); };
$("connectAllBtn").onclick = async () => { await api("POST", "/api/connect-all"); };
$("disconnectAllBtn").onclick = async () => { await api("POST", "/api/disconnect-all"); };

if (TOKEN) enterApp();
```

- [ ] **Step 5: 确认 `web_server.py` 顶部已 import `FileResponse`、`StaticFiles`**

检查 import 区包含：

```python
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
```

- [ ] **Step 6: 启动验证**

Run: `.venv/bin/python -c "from web_server import create_app; create_app('secret'); print('app builds ok')"`
Expected: `app builds ok`

- [ ] **Step 7: Commit**

```bash
git add web/ web_server.py
git commit -m "feat(web): 静态前端单页与会话管理界面"
```

---

### Task 11: 启动入口与 CLI 参数

**Files:**
- Modify: `web_server.py`（追加 `if __name__ == "__main__"` 与 `argparse`）

**Interfaces:**
- Produces: `python web_server.py --token xxx [--host 0.0.0.0] [--port 8741]` 启动服务；token 也可来自 `SSH_TUNNEL_TOKEN` 环境变量。

- [ ] **Step 1: 在 `web_server.py` 末尾追加启动逻辑**

```python
def main():
    parser = argparse.ArgumentParser(description="SSH Tunnel Manager Web Server")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8741)
    parser.add_argument("--token", default=os.environ.get("SSH_TUNNEL_TOKEN", ""))
    parser.add_argument("--config", default=None, help="sessions.json path")
    args = parser.parse_args()
    if not args.token:
        raise SystemExit("Error: token required. Use --token or set SSH_TUNNEL_TOKEN.")
    app = create_app(token=args.token, config_path=args.config)
    import uvicorn
    uvicorn.run(app, host=args.host, port=args.port)


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: 验证无 token 拒绝启动**

Run: `.venv/bin/python web_server.py 2>&1 | head -3`
Expected: 包含 `token required`

- [ ] **Step 3: 运行全部测试**

Run: `.venv/bin/pytest tests/ -v`
Expected: 全部 passed

- [ ] **Step 4: Commit**

```bash
git add web_server.py
git commit -m "feat(web): 启动入口与 CLI 参数"
```

---

### Task 12: 端到端验证与文档

**Files:**
- Modify: `README_zh.md`（追加 Web 运行说明）

**Interfaces:**
- Produces: README 含 Web 启动方式；端到端验证清单通过。

- [ ] **Step 1: 在 `README_zh.md` "安装与运行" 章节后追加 Web 小节**

```markdown
#### 通过浏览器管理（B/S 架构）

无需图形界面，适合部署在服务器上通过浏览器管理端口转发。

```shell
python3.12 -m venv .venv
.venv/bin/pip install -r requirements-web.txt
.venv/bin/python web_server.py --token 你的口令
# 浏览器访问 http://<服务器IP>:8741 ，输入口令登录
```

可选参数：`--host`（默认 `0.0.0.0`）、`--port`（默认 `8741`）、`--config`（指定 sessions.json 路径）。
口令也可用环境变量 `SSH_TUNNEL_TOKEN` 传入。

> GUI 与 Web 共享同一份 `~/.ssh_tunnel_manager/sessions.json`，不建议两者同时编辑会话配置。
```

- [ ] **Step 2: 端到端验证清单（手动/自动）**

逐项确认：
1. GUI 正常：`.venv/bin/python -m py_compile ssh_tunnel_manager.py` 通过
2. 全部测试通过：`.venv/bin/pytest tests/ -q`
3. 服务可启动（后台短跑）：`timeout 3 .venv/bin/python web_server.py --token t --port 8742; echo exit-ok`
4. core 无 tkinter：`.venv/bin/python -c "import core.models, core.config, core.ssh_manager"`

- [ ] **Step 3: Commit**

```bash
git add README_zh.md
git commit -m "docs: 补充 Web 运行说明与端到端验证"
```

---

## Self-Review

**1. Spec coverage:**
- §3 核心拆分 → Task 2/3/4 ✓
- §4 Web 层（启动/鉴权/API/SSE）→ Task 6/7/8/9/11 ✓
- §5 前端 → Task 10 ✓
- §6 GUI 兼容 → Task 5 ✓
- §7 共享配置并发限制 → Task 12 文档说明 ✓（无锁为有意限制）
- §8 YAGNI 范围 → 计划未引入用户体系/文件锁/HTTPS/npm ✓
- §10 验证标准 → Task 12 ✓

**2. Placeholder scan:** 无 TBD/TODO；每个代码步骤均含完整代码 ✓

**3. Type consistency:** `SSHSession.from_dict`、`ConfigManager.add_session`、`SSHProcessManager.subscribe/is_active/_notify` 在各 Task 间签名一致 ✓
