# SSH Tunnel Manager — B/S 架构改造设计

- 日期：2026-06-26
- 分支：`bs-architecture`（基于 `main`）
- 目标：在保留桌面 GUI 的前提下，新增 B/S（浏览器/服务器）架构，通过 Web 界面管理 SSH 端口转发。

## 1. 背景与动机

现有项目是单文件 Tkinter 桌面 GUI 应用（`ssh_tunnel_manager.py`），通过调用系统 `ssh` 命令管理本地/远程/动态端口转发。核心逻辑（配置持久化、SSH 进程管理）与 GUI 耦合在同一文件，且模块级 `import tkinter` 导致无法在无图形界面的远程服务器上运行。

需求：能在浏览器中管理端口转发，部署到 headless 服务器，同时保留原桌面 GUI。

## 2. 架构总览

把现有单文件拆成"核心层 + 两个前端"，核心层被 GUI 和 Web 共享。

```
ssh-tunnel-manager/
├── core/
│   ├── __init__.py
│   ├── models.py        # PortForwardRule, SSHSession（dataclass）
│   ├── config.py        # ConfigManager（持久化 + 端口冲突校验）
│   └── ssh_manager.py   # SSHProcessManager（构建命令/启停/保活/轮询）
├── ssh_tunnel_manager.py # GUI 入口，改 import core（行为不变）
├── web_server.py         # 新增：FastAPI Web 入口
├── web/                  # 新增：静态前端
│   ├── index.html
│   ├── app.js
│   └── style.css
└── requirements-web.txt  # 新增：fastapi, uvicorn[standard]
```

核心层不 import tkinter，使 Web 能在无图形界面环境运行。

## 3. 核心层拆分

### 3.1 core/models.py
搬出原文件 24-75 行的两个 dataclass：
- `PortForwardRule`：direction / local_port / remote_host / remote_port / description
- `SSHSession`：name / host / port / username / auth_method / key_path / password / remote_cmd / keepalive_enabled / keepalive_interval / forward_rules / enabled / created_at / updated_at

保留 `to_dict()` / `from_dict()`，去掉对 tkinter 的任何依赖。

### 3.2 core/config.py
搬出原文件 77-147 行 `ConfigManager`：
- 持久化 `~/.ssh_tunnel_manager/sessions.json`
- `add_session` 含本地端口冲突校验
- `list_sessions` / `get_session` / `remove_session` / `save`

### 3.3 core/ssh_manager.py
搬出原文件 151-378 行 `SSHProcessManager`。唯一行为变更：将 `_poll_callback`（单回调）升级为**事件分发**：
- 维护一个订阅者列表 `_subscribers: list[Callable]`
- 新增 `subscribe(callback) -> unsubscribe_fn`，`_notify(event)` 向所有订阅者推送
- 状态变化（active/exited/error）与启停日志通过事件分发
- GUI 侧改为传入自己的回调（等价于订阅一个回调），行为不变
- `start_session`/`stop_session` 的日志 callback 保持不变

事件结构（dict）：
- `{"type": "status", "name": str, "status": "active"|"exited"|"error", "detail": str}`
- `{"type": "log", "name": str, "lines": str}`

## 4. Web 层（web_server.py）

复用 `ConfigManager` + `SSHProcessManager`，应用级单例，启动时载入 `sessions.json`。

### 4.1 启动与配置
- 默认绑定 `0.0.0.0:8741`，可用 `--host/--port` 覆盖
- 鉴权口令来源优先级：`--token` 参数 > `SSH_TUNNEL_TOKEN` 环境变量
- 未设置 token 时拒绝启动并提示（局域网暴露必须有鉴权）

### 4.2 鉴权
- `POST /api/login`：body `{token}`，校验通过返回 `{token}`，前端存 localStorage
- 除 `/api/login`、静态资源、`/api/events` 外，所有 API 要求 `Authorization: Bearer <token>`
- SSE 端点 `/api/events` 用 query 参数 `?token=` 校验（EventSource 不支持自定义 header）

### 4.3 REST API
| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/api/login` | 校验 token |
| GET | `/api/sessions` | 列出会话（含运行状态） |
| POST | `/api/sessions` | 新建/更新会话（body 为 session dict） |
| DELETE | `/api/sessions/{name}` | 删除会话 |
| POST | `/api/sessions/{name}/connect` | 启动隧道 |
| POST | `/api/sessions/{name}/disconnect` | 停止隧道 |
| POST | `/api/connect-all` | 批量启动 |
| POST | `/api/disconnect-all` | 批量停止 |
| GET | `/api/sessions/{name}/logs` | 拉取连接日志 |
| GET | `/api/events` | SSE 流：status + log 事件 |

### 4.4 状态来源
`SSHProcessManager.active_processes` 提供"运行中"集合；`list_sessions` 返回时附加 `status`（active/idle）字段。状态变化通过 SSE 实时推送。

## 5. 前端（web/，原生 HTML/JS 单页）

- 单页布局：左侧会话列表（含状态指示），右侧详情（端口转发规则表格 + 日志区）+ 操作按钮
- 登录界面 → 存 token 到 localStorage → 后续请求带 `Authorization` header
- `EventSource('/api/events?token=...')`，收到 `status` 事件更新列表项，收到 `log` 事件追加日志
- 功能对齐 GUI：
  - 增删改会话（含认证方式 password/key、keepalive 配置）
  - 端口转发规则增删改（local/remote/dynamic）
  - 单个启停、批量启停
  - 实时状态、连接日志
- 端口冲突由后端 `ConfigManager.add_session` 校验，错误信息回显

## 6. GUI 兼容性

- 拆分后 `ssh_tunnel_manager.py` 改为 `from core.models import ...` / `from core.config import ...` / `from core.ssh_manager import ...`
- GUI 行为保持不变；`SSHProcessManager` 的事件分发改造对 GUI 透明（GUI 传入的 callback 等价订阅）
- 验证方式：用原命令 `/usr/local/bin/python3.12 ssh_tunnel_manager.py` 启动 GUI，确认会话增删改、连接启停、状态刷新、日志均正常

## 7. 共享配置与并发限制

- GUI 与 Web 读写同一份 `~/.ssh_tunnel_manager/sessions.json`
- `ConfigManager.save()` 是全量覆盖写，**并发写存在竞态**：MVP 阶段为单用户本地工具，不引入文件锁
- 限制（写入 spec 供用户知晓）：不建议 GUI 和 Web 同时编辑会话配置；同时读取 + 一方编辑可接受
- `SSHProcessManager` 为进程内单例，Web 进程退出则其管理的隧道断开

## 8. 范围限制（YAGNI）

- 不做用户体系，单 token 鉴权
- 不做写冲突保护（文件锁/乐观锁）
- 不做 HTTPS/反向代理，局域网内明文 HTTP（如需可后续套反代）
- 不引入 npm/前端构建步骤
- 不做会话导入导出、克隆（GUI 已有克隆，Web MVP 可不含；后续按需补）

## 9. 依赖

- `requirements-web.txt`：`fastapi`、`uvicorn[standard]`
- 运行：`/usr/local/bin/python3.12 -m uvicorn` 或 `python3.12 web_server.py`
- GUI 仍为零第三方依赖

## 10. 验证标准

1. 拆分后 GUI 启动正常，原有功能（增删改会话、启停隧道、状态刷新、日志）全部可用
2. `web_server.py` 启动后浏览器访问能登录、列出/新建/编辑/删除会话
3. 浏览器中启动隧道后，本地端口转发实际生效（可用 `curl`/`nc` 验证）
4. SSE 推送：启停隧道时前端列表状态实时变化、日志实时追加
5. GUI 与 Web 共享同一 `sessions.json`，一边新建的会话另一边可见
