[English](README.md) | [中文](README_zh.md)

# 一个基于 Python + Tkinter 的桌面 GUI 应用，用于管理 SSH 会话和端口转发隧道。

![Python](https://img.shields.io/badge/python-3.11+-blue.svg)
![License](https://img.shields.io/badge/license-MIT-green.svg)
![Platform](https://img.shields.io/badge/platform-Windows%2C%20macOS%2C%20Linux-lightgrey.svg)

### 主要功能

- **SSH 会话管理** — 创建、编辑、复制和删除 SSH 连接
- **端口转发** — 支持本地转发 (`-L`)、远程转发 (`-R`) 和动态转发 (`-D`)
- **认证方式** — 支持密码认证和 SSH 密钥认证
- **心跳保活** — 可配置的心跳间隔，防止连接超时断开
- **批量操作** — 一键连接/断开所有会话
- **持久化配置** — 会话信息保存在本地 JSON 文件中 (`~/.ssh_tunnel_manager/sessions.json`)

### 环境要求

- Python 3.11+
- Windows 10/11、macOS 12+ 或 Linux
- 系统 PATH 中包含 `ssh` 客户端
- 可选：`sshpass`（用于非 Windows 平台的密码认证）

### 安装与运行

#### 从源码运行

```shell
git clone https://github.com/Doitky/ssh-tunnel-manager.git
cd ssh-tunnel-manager
python ssh_tunnel_manager.py
```

#### 打包为独立可执行文件

**Windows**

```shell
pip install pyinstaller
pyinstaller --onefile --windowed ssh_tunnel_manager.py
# 输出: dist/ssh-tunnel-manager.exe
```

**macOS**

> **注意：** 请使用 Homebrew 安装的 Python 3.12+，系统自带的 Python 3.9 的 Tkinter 在 macOS 15 上会导致崩溃。

```shell
# 确保使用 Homebrew Python
export PATH="/usr/local/opt/python@3.12/bin:$PATH"  # Intel Mac
# export PATH="/opt/homebrew/opt/python@3.12/bin:$PATH"  # Apple Silicon Mac

pip install pyinstaller
pyinstaller --onefile --windowed --name SSH-Tunnel-Manager ssh_tunnel_manager.py
# 输出: dist/SSH-Tunnel-Manager
```

**Linux**

```shell
pip install pyinstaller
pyinstaller --onefile --windowed --name ssh-tunnel-manager ssh_tunnel_manager.py
# 输出: dist/ssh-tunnel-manager
```

#### 安装 sshpass（macOS / Linux）

用于密码认证：

```shell
# macOS
brew install sshpass

# Ubuntu / Debian
sudo apt-get install sshpass

# Fedora / RHEL
sudo dnf install sshpass
```

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

### 使用方法

1. 点击 **+ New Session** 创建新连接
2. 填写 **General** 标签页（主机、端口、用户名、认证方式）
3. 可选：在 **Port Forwarding** 标签页配置端口转发规则
4. 可选：启用 **Keepalive** 并设置心跳间隔
5. 点击 **Save** 保存，双击会话或选中后点击 **Connect** 即可连接

### 配置文件位置

会话数据存储在：

```
%USERPROFILE%\.ssh_tunnel_manager\sessions.json        (Windows)
~/.ssh_tunnel_manager/sessions.json                    (macOS / Linux)
```

### 许可证

MIT License。详见 [LICENSE](LICENSE)。
