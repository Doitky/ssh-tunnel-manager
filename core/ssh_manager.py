import os
import shutil
import socket
import subprocess
import threading
import time
import sys
from datetime import datetime
from typing import Optional

from core.config import ConfigManager
from core.models import SSHSession


class SSHProcessManager:
    """Manages SSH processes and keepalive."""

    def __init__(self, config: ConfigManager):
        self.config = config
        self.active_processes: dict[str, dict] = {}
        self._keepalive_timers: dict[str, threading.Timer] = {}
        self._polling_thread: Optional[threading.Thread] = None
        self._polling_stop_event = threading.Event()
        self._poll_callback = None  # callable(name, status, detail)
        self._subscribers: list = []

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

    def is_active(self, session_name: str) -> bool:
        info = self.active_processes.get(session_name)
        if info is None:
            return False
        proc = info.get("proc")
        return proc is not None and proc.poll() is None

    def check_local_ports_in_use(self, session: "SSHSession") -> list[int]:
        """检测本会话需要在本地监听的端口是否已被占用。

        仅检测 local / dynamic 方向（这两类由 SSH 在本地监听）。
        remote 方向的端口在远程主机监听，本地不监听，故不检测。
        返回被占用的端口列表（升序）。
        """
        ports: set[int] = set()
        for rule in session.forward_rules:
            if rule.direction in ("local", "dynamic") and rule.local_port > 0:
                ports.add(rule.local_port)
        in_use: list[int] = []
        for port in sorted(ports):
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            try:
                sock.bind(("127.0.0.1", port))
            except OSError:
                in_use.append(port)
            finally:
                sock.close()
        return in_use

    def start_session(self, session_name: str, callback=None):
        session = self.config.get_session(session_name)
        if session is None:
            if callback:
                callback(f"Error: Session '{session_name}' not found.")
            return
        if not session.enabled:
            if callback:
                callback(f"Error: Session '{session_name}' is disabled.")
            return
        if session_name in self.active_processes:
            if callback:
                callback(f"Warning: Session '{session_name}' is already active.")
            return

        cmd = self._build_ssh_command(session)
        if cmd is None:
            if callback:
                callback("Error: Could not build SSH command.")
            return

        # 连接前检测本地监听端口是否被占用，避免端口冲突导致连接失败
        busy_ports = self.check_local_ports_in_use(session)
        if busy_ports:
            msg = f"Error: 本地端口已被占用: {', '.join(str(p) for p in busy_ports)}"
            if callback:
                callback(msg)
            self._notify({"type": "log", "name": session_name, "lines": msg})
            return

        connect_event = threading.Event()
        self.active_processes[session_name] = {
            "proc": None,
            "session": session,
            "start_time": datetime.now(),
            "thread": threading.current_thread(),
            "_connect_event": connect_event,
            "_stop_requested": False,
            "_reconnect_attempts": 0,
        }

        def _run():
            log_lines = []

            def _launch_once():
                """启动一次 SSH 进程并阻塞至其退出。返回退出码（None 表示异常）。"""
                log_lines.append(f"[{datetime.now().strftime('%H:%M:%S')}] Starting session: {session_name}")
                log_lines.append(f"Command: {' '.join(cmd)}")
                env = os.environ.copy()
                popen_kwargs = {
                    "stdin": subprocess.PIPE,
                    "stdout": subprocess.PIPE,
                    "stderr": subprocess.DEVNULL,
                    "text": True,
                    "env": env,
                }
                if sys.platform == "win32":
                    popen_kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
                proc = subprocess.Popen(cmd, **popen_kwargs)
                self.active_processes[session_name]["proc"] = proc
                self.active_processes[session_name]["thread"] = threading.current_thread()
                self.active_processes[session_name]["_last_poll_status"] = "starting"
                connect_event.set()
                if session.keepalive_enabled:
                    self._start_keepalive(session_name)
                if session.auth_method == "password" and not shutil.which("sshpass"):
                    log_lines.append("Warning: sshpass not found. Password authentication will fail.")
                    log_lines.append("Install sshpass: brew install sshpass (macOS) or apt-get install sshpass (Linux)")
                    log_lines.append("Or switch to key-based authentication in session settings.")
                def _reader():
                    for line in proc.stdout:
                        log_lines.append(line.rstrip())
                        time.sleep(0.01)
                reader_thread = threading.Thread(target=_reader, daemon=True)
                reader_thread.start()
                proc.wait()
                return proc.returncode

            try:
                while True:
                    try:
                        _launch_once()
                    except Exception as e:
                        log_lines.append(f"Error: {e}")
                        self._stop_keepalive(session_name)

                    info = self.active_processes.get(session_name)
                    if info is None:
                        break  # 已被外部清理
                    # 用户主动断开 → 不重连
                    if info.get("_stop_requested"):
                        break
                    # 未开启自动重连 → 退出
                    if not session.auto_reconnect:
                        break
                    # 重新读取配置，间隔等待重连（期间可被 stop 取消）
                    interval = session.reconnect_interval or 10
                    info["_reconnect_attempts"] += 1
                    n = info["_reconnect_attempts"]
                    msg = f"[{datetime.now().strftime('%H:%M:%S')}] 连接断开，{interval}s 后自动重连（第 {n} 次）..."
                    log_lines.append(msg)
                    self._notify({"type": "log", "name": session_name, "lines": "\n".join(log_lines)})
                    # 间隔等待，每秒检查是否被主动停止
                    for _ in range(interval):
                        if info.get("_stop_requested"):
                            break
                        time.sleep(1)
                    if info.get("_stop_requested"):
                        break
            finally:
                self._stop_keepalive(session_name)
                self.active_processes.pop(session_name, None)
                if callback:
                    callback("\n".join(log_lines))
                self._notify({"type": "log", "name": session_name, "lines": "\n".join(log_lines)})

        t = threading.Thread(target=_run, daemon=True)
        t.start()

    def stop_session(self, session_name: str):
        info = self.active_processes.get(session_name)
        if info is None:
            return False
        info["_stop_requested"] = True
        proc = info["proc"]
        self._stop_keepalive(session_name)
        try:
            if proc is not None:
                proc.terminate()
                proc.wait(timeout=5)
            else:
                del self.active_processes[session_name]
                return True
        except subprocess.TimeoutExpired:
            if proc is not None:
                proc.kill()
                proc.wait(timeout=3)
        except Exception:
            pass
        finally:
            self.active_processes.pop(session_name, None)
        return True

    def _build_ssh_command(self, session: SSHSession) -> Optional[list[str]]:
        cmd = []
        if session.auth_method == "password" and session.password:
            sshpass_bin = shutil.which("sshpass") or (
                os.path.exists("/opt/homebrew/bin/sshpass") and "/opt/homebrew/bin/sshpass"
            ) or (
                os.path.exists("/usr/local/bin/sshpass") and "/usr/local/bin/sshpass"
            )
            if sshpass_bin:
                cmd.extend([sshpass_bin, "-p", session.password, "ssh"])
        if not cmd:
            cmd = ["ssh"]
            cmd.extend(["-o", "BatchMode=yes"])
        if session.port != 22:
            cmd.extend(["-p", str(session.port)])
        if session.auth_method == "key" and session.key_path:
            cmd.extend(["-i", session.key_path])
        cmd.extend([
            "-o", "ServerAliveInterval={}".format(session.keepalive_interval),
            "-o", "ServerAliveCountMax=3",
            "-o", "StrictHostKeyChecking=no",
            "-o", "UserKnownHostsFile=/dev/null",
        ])
        for rule in session.forward_rules:
            if rule.direction == "local":
                cmd.extend(["-L", f"{rule.local_port}:{rule.remote_host}:{rule.remote_port}"])
            elif rule.direction == "remote":
                cmd.extend(["-R", f"{rule.local_port}:{rule.remote_host}:{rule.remote_port}"])
            elif rule.direction == "dynamic":
                cmd.extend(["-D", f"{rule.local_port}"])
        cmd.extend([f"{session.username}@{session.host}"])
        if session.remote_cmd:
            cmd.extend(["-t", session.remote_cmd])
        return cmd

    def _start_keepalive(self, session_name: str):
        session = self.config.get_session(session_name)
        if not session or not session.keepalive_enabled:
            return
        def _ping_loop():
            interval = session.keepalive_interval
            while session_name in self.active_processes:
                time.sleep(interval)
                info = self.active_processes.get(session_name)
                if info and info["proc"].poll() is None:
                    try:
                        info["proc"].stdin.write(f"echo keepalive_{int(time.time())}\n")
                        info["proc"].stdin.flush()
                    except Exception:
                        pass
        t = threading.Thread(target=_ping_loop, daemon=True)
        t.start()
        self._keepalive_timers[session_name] = t

    def _stop_keepalive(self, session_name: str):
        self._keepalive_timers.pop(session_name, None)

    # ─── Connection Status Polling ───────────────────────────────────────────

    def start_polling(self, callback=None, interval: float = 2.0):
        """Start a background thread that periodically checks SSH process health.

        The callback signature: callback(session_name: str, status: str, detail: str)
          - status: "active" | "exited" | "error"
        """
        self._poll_callback = callback
        self._poll_interval = interval
        self._polling_stop_event.clear()

        def _poll_loop():
            while not self._polling_stop_event.is_set():
                for name, info in list(self.active_processes.items()):
                    proc = info.get("proc")
                    if proc is None:
                        continue
                    rc = proc.poll()
                    if rc is not None:
                        # Process has exited
                        detail = f"Exited with code {rc}"
                        if self._poll_callback:
                            self._poll_callback(name, "exited", detail)
                        self._notify({"type": "status", "name": name, "status": "exited", "detail": detail})
                        self._stop_keepalive(name)
                        self.active_processes.pop(name, None)
                    elif rc is None and info.get("_last_poll_status") != "active":
                        # Just became active (first successful poll)
                        if self._poll_callback:
                            self._poll_callback(name, "active", "Running")
                        self._notify({"type": "status", "name": name, "status": "active", "detail": "Running"})
                        info["_last_poll_status"] = "active"
                self._polling_stop_event.wait(self._poll_interval)

        t = threading.Thread(target=_poll_loop, daemon=True, name="SSH-Poller")
        t.start()
        self._polling_thread = t

    def stop_polling(self):
        """Stop the background polling thread."""
        self._polling_stop_event.set()
        if self._polling_thread and self._polling_thread.is_alive():
            self._polling_thread.join(timeout=5)
        self._polling_thread = None
        self._poll_callback = None
