import os
import shutil
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

        connect_event = threading.Event()
        self.active_processes[session_name] = {
            "proc": None,
            "session": session,
            "start_time": datetime.now(),
            "thread": threading.current_thread(),
            "_connect_event": connect_event,
        }

        def _run():
            log_lines = []
            try:
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
            except Exception as e:
                log_lines.append(f"Error: {e}")
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
