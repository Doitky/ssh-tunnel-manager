#!/usr/bin/env python3
"""
SSH Tunnel Manager - A MobaXterm-like GUI application
Features: SSH session management, port forwarding tunnels, keepalive/anti-idle
"""

import tkinter as tk
from tkinter import ttk, messagebox, scrolledtext
import json
import os
import shutil
import subprocess
import threading
import time
import sys
from datetime import datetime
from dataclasses import dataclass, field, asdict
from typing import Optional


# ─── Data Models ───────────────────────────────────────────────────────────────

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


# ─── Config Manager ────────────────────────────────────────────────────────────

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
        # Check local_port conflicts with existing sessions
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

# ─── SSH Process Manager ───────────────────────────────────────────────────────

class SSHProcessManager:
    """Manages SSH processes and keepalive."""

    def __init__(self, config: ConfigManager):
        self.config = config
        self.active_processes: dict[str, dict] = {}
        self._keepalive_timers: dict[str, threading.Timer] = {}
        self._polling_thread: Optional[threading.Thread] = None
        self._polling_stop_event = threading.Event()
        self._poll_callback = None  # callable(name, status, detail)

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
                if session.auth_method == "password" and not self._find_sshpass():
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

    def _find_sshpass(self) -> Optional[str]:
        """Find sshpass binary, checking bundled location first."""
        # Check bundled sshpass in .app Resources directory
        # When running as PyInstaller app:
        #   - sys.executable -> Contents/MacOS/SSH Tunnel Manager
        #   - __file__ -> Contents/Resources/ssh_tunnel_manager.py
        # So sshpass should be at Contents/Resources/sshpass
        resource_dir = os.path.dirname(os.path.abspath(__file__))
        bundled = os.path.join(resource_dir, "sshpass")
        if os.path.exists(bundled):
            return bundled
        # Fall back to system PATH
        found = shutil.which("sshpass")
        if found:
            return found
        # Homebrew paths
        if os.path.exists("/opt/homebrew/bin/sshpass"):
            return "/opt/homebrew/bin/sshpass"
        if os.path.exists("/usr/local/bin/sshpass"):
            return "/usr/local/bin/sshpass"
        return None

    def _build_ssh_command(self, session: SSHSession) -> Optional[list[str]]:
        cmd = []
        if session.auth_method == "password" and session.password:
            sshpass_bin = self._find_sshpass()
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
                        self._stop_keepalive(name)
                        self.active_processes.pop(name, None)
                    elif rc is None and info.get("_last_poll_status") != "active":
                        # Just became active (first successful poll)
                        if self._poll_callback:
                            self._poll_callback(name, "active", "Running")
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


class SessionDialog(tk.Toplevel):
    def __init__(self, parent, session=None):
        super().__init__(parent)
        self.session = session or SSHSession()
        self.result = None
        self._build_ui()

    def _build_ui(self):
        self.title('SSH Session Configuration' if not self.session.name else f'Edit Session: {self.session.name}')
        self.geometry('700x600')
        self.resizable(True, True)
        main_frame = ttk.Frame(self, padding=15)
        main_frame.pack(fill=tk.BOTH, expand=True)
        notebook = ttk.Notebook(main_frame)
        notebook.pack(fill=tk.BOTH, expand=True)
        general = ttk.Frame(notebook, padding=10)
        notebook.add(general, text='General')
        fields_general = [('Name:', 'name', 'Session display name'), ('Host:', 'host', 'SSH server hostname/IP'), ('Port:', 'port', 'SSH port (default 22)'), ('Username:', 'username', 'Login username')]
        self._entry_vars = {}
        for i, (label, attr, hint) in enumerate(fields_general):
            ttk.Label(general, text=label).grid(row=i, column=0, sticky=tk.W, pady=3)
            var = tk.StringVar(value=str(getattr(self.session, attr, '')))
            self._entry_vars[attr] = var
            entry = ttk.Entry(general, textvariable=var, width=40)
            entry.grid(row=i, column=1, pady=3, padx=(5, 0))
            ttk.Label(general, text=hint, foreground='gray').grid(row=i, column=2, sticky=tk.W, padx=(5, 0))
        ttk.Label(general, text='Auth:').grid(row=5, column=0, sticky=tk.W, pady=3)
        auth_var = tk.StringVar(value=self.session.auth_method)
        self._entry_vars['auth_method'] = auth_var
        ttk.Combobox(general, textvariable=auth_var, values=['password', 'key'], width=37, state='readonly').grid(row=5, column=1, pady=3, padx=(5, 0))
        ttk.Label(general, text='Key Path:').grid(row=6, column=0, sticky=tk.W, pady=3)
        key_var = tk.StringVar(value=self.session.key_path)
        self._entry_vars['key_path'] = key_var
        key_frame = ttk.Frame(general)
        key_frame.grid(row=6, column=1, sticky=tk.W, padx=(5, 0))
        ttk.Entry(key_frame, textvariable=key_var, width=32).pack(side=tk.LEFT)
        ttk.Button(key_frame, text='Browse...', command=lambda: self._browse_key()).pack(side=tk.LEFT, padx=(5, 0))
        ttk.Label(general, text='Password:').grid(row=7, column=0, sticky=tk.W, pady=3)
        pw_var = tk.StringVar(value=self.session.password)
        self._entry_vars['password'] = pw_var
        ttk.Entry(general, textvariable=pw_var, width=40, show='*').grid(row=7, column=1, pady=3, padx=(5, 0))
        ttk.Label(general, text='Remote Cmd:').grid(row=8, column=0, sticky=tk.NW, pady=3)
        cmd_var = tk.StringVar(value=self.session.remote_cmd)
        self._entry_vars['remote_cmd'] = cmd_var
        ttk.Entry(general, textvariable=cmd_var, width=40).grid(row=8, column=1, sticky=tk.EW, pady=3, padx=(5, 0))
        en_var = tk.BooleanVar(value=self.session.enabled)
        self._entry_vars['enabled'] = en_var
        ttk.Checkbutton(general, text='Enable session', variable=en_var).grid(row=9, column=0, columnspan=3, sticky=tk.W, pady=5)
        keepalive = ttk.Frame(notebook, padding=10)
        notebook.add(keepalive, text='Keepalive')
        ka_var = tk.BooleanVar(value=self.session.keepalive_enabled)
        self._entry_vars['keepalive_enabled'] = ka_var
        ttk.Checkbutton(keepalive, text='Enable keepalive (anti-idle)', variable=ka_var).pack(anchor=tk.W, pady=5)
        interval_var = tk.IntVar(value=self.session.keepalive_interval)
        self._entry_vars['keepalive_interval'] = interval_var
        ttk.Label(keepalive, text='Keepalive interval (seconds):').pack(anchor=tk.W, pady=5)
        spin = ttk.Spinbox(keepalive, from_=5, to=300, textvariable=interval_var, width=20)
        spin.pack(anchor=tk.W, pady=5)
        ttk.Label(keepalive, text='Sends periodic ping to prevent connection timeout/idle disconnect.', foreground='gray', wraplength=500).pack(anchor=tk.W, pady=5)
        pf = ttk.Frame(notebook, padding=10)
        notebook.add(pf, text='Port Forwarding')
        self.forward_rules = self.session.forward_rules or []
        self._build_forwarding_ui(pf)
        btn_frame = ttk.Frame(main_frame)
        btn_frame.pack(fill=tk.X, pady=(10, 0))
        ttk.Button(btn_frame, text='Save', command=self._save).pack(side=tk.RIGHT, padx=(5, 0))
        ttk.Button(btn_frame, text='Cancel', command=self.destroy).pack(side=tk.RIGHT)

    def _build_forwarding_ui(self, parent):
        ttk.Label(parent, text='Port Forwarding Rules (optional):').pack(anchor=tk.W, pady=(0, 5))
        rules_frame = ttk.Frame(parent)
        rules_frame.pack(fill=tk.BOTH, expand=True)
        columns = ('direction', 'local_port', 'remote_host', 'remote_port', 'description')
        self.rules_tree = ttk.Treeview(rules_frame, columns=columns, show='headings', height=6)
        for col in columns:
            heading = {'direction': 'Direction', 'local_port': 'Local Port', 'remote_host': 'Remote Host', 'remote_port': 'Remote Port', 'description': 'Description'}
            self.rules_tree.heading(col, text=heading[col])
            self.rules_tree.column(col, width=100)
        self.rules_tree.pack(fill=tk.BOTH, expand=True, pady=(0, 5))
        btn_row = ttk.Frame(parent)
        btn_row.pack(fill=tk.X)
        ttk.Button(btn_row, text='Add Local', command=lambda: self._add_rule('local')).pack(side=tk.LEFT, padx=2)
        ttk.Button(btn_row, text='Add Remote', command=lambda: self._add_rule('remote')).pack(side=tk.LEFT, padx=2)
        ttk.Button(btn_row, text='Add Dynamic', command=lambda: self._add_rule('dynamic')).pack(side=tk.LEFT, padx=2)
        ttk.Button(btn_row, text='Edit', command=self._edit_rule).pack(side=tk.LEFT, padx=2)
        ttk.Button(btn_row, text='Remove', command=self._remove_rule).pack(side=tk.LEFT, padx=2)
        self._refresh_rules_display()

    def _add_rule(self, direction='local'):
        rule = PortForwardRule(direction=direction)
        self._show_rule_editor(rule, lambda r: self._save_rule(r))

    def _edit_rule(self):
        sel = self.rules_tree.selection()
        if not sel:
            messagebox.showwarning('No Selection', 'Please select a rule to edit.')
            return
        idx = int(sel[0].split('.')[-1])
        rule = self.forward_rules[idx]
        self._show_rule_editor(rule, lambda r: self._save_rule(r, idx))

    def _remove_rule(self):
        sel = self.rules_tree.selection()
        if not sel:
            return
        idx = int(sel[0].split('.')[-1])
        self.forward_rules.pop(idx)
        self._refresh_rules_display()

    def _save_rule(self, rule, idx=None):
        if idx is None:
            self.forward_rules.append(rule)
        else:
            self.forward_rules[idx] = rule
        self._refresh_rules_display()

    def _show_rule_editor(self, rule, on_save):
        win = tk.Toplevel(self)
        win.title('Port Forwarding Rule')
        win.geometry('420x380')
        win.transient(self)
        win.grab_set()
        body = ttk.Frame(win, padding=15)
        body.pack(fill=tk.BOTH, expand=True)
        fields = [('Direction:', 'direction', ['local', 'remote', 'dynamic'], True), ('Local Port:', 'local_port', None, False), ('Remote Host:', 'remote_host', None, False), ('Remote Port:', 'remote_port', None, False), ('Description:', 'description', None, False)]
        vars = {}
        for i, (label_text, attr, choices, readonly) in enumerate(fields):
            ttk.Label(body, text=label_text).grid(row=i, column=0, sticky=tk.W, pady=3)
            if choices:
                var = tk.StringVar(value=getattr(rule, attr))
                ttk.Combobox(body, textvariable=var, values=choices, state='readonly' if readonly else 'normal', width=35).grid(row=i, column=1, sticky=tk.EW, pady=3, padx=(5, 0))
            else:
                var = tk.StringVar(value=str(getattr(rule, attr, '')))
                ttk.Entry(body, textvariable=var, width=35).grid(row=i, column=1, sticky=tk.EW, pady=3, padx=(5, 0))
            vars[attr] = var
        btn_frame = ttk.Frame(win)
        btn_frame.pack(fill=tk.X, padx=15, pady=(0, 12))
        def save():
            rule.direction = vars['direction'].get()
            rule.local_port = int(vars['local_port'].get() or 0)
            rule.remote_host = vars['remote_host'].get()
            rule.remote_port = int(vars['remote_port'].get() or 0)
            rule.description = vars['description'].get()
            on_save(rule)
            win.destroy()
        ttk.Button(btn_frame, text='Save', command=save).pack(side=tk.RIGHT, padx=(5, 0))
        ttk.Button(btn_frame, text='Cancel', command=win.destroy).pack(side=tk.RIGHT)

    def _refresh_rules_display(self):
        for item in self.rules_tree.get_children():
            self.rules_tree.delete(item)
        for i, rule in enumerate(self.forward_rules):
            self.rules_tree.insert('', tk.END, iid=f'{i}', values=(rule.direction, rule.local_port, rule.remote_host, rule.remote_port, rule.description))

    def _browse_key(self):
        from tkinter.filedialog import askopenfilename
        path = askopenfilename(title='Select SSH Private Key', filetypes=[('Private Keys', '*.pem *.key *.ppk'), ('All Files', '*.*')])
        if path:
            self._entry_vars['key_path'].set(path)

    def _save(self):
        for attr, var in self._entry_vars.items():
            val = var.get()
            if attr in ('port', 'local_port', 'remote_port', 'keepalive_interval'):
                try:
                    val = int(val)
                except ValueError:
                    val = 0
            elif attr == 'enabled':
                val = var.get()
            elif attr == 'keepalive_enabled':
                val = var.get()
            setattr(self.session, attr, val)
        self.session.forward_rules = self.forward_rules
        self.result = self.session
        self.destroy()


class LogWindow(ttk.Frame):
    def __init__(self, parent):
        super().__init__(parent)
        self.text = scrolledtext.ScrolledText(self, state='disabled', font=('Consolas', 9), height=8)
        self.text.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)

    def append(self, message):
        self.text.configure(state=tk.NORMAL)
        self.text.insert(tk.END, message + '\n')
        self.text.see(tk.END)
        self.text.configure(state=tk.DISABLED)

    def clear(self):
        self.text.configure(state=tk.NORMAL)
        self.text.delete('1.0', tk.END)
        self.text.configure(state=tk.DISABLED)

class SSHTunnelManagerApp:
    def __init__(self, root):
        self.root = root
        self.root.title("SSH Tunnel Manager")
        self.root.geometry("1100x700")
        self.root.minsize(800, 500)
        self.config = ConfigManager()
        self.ssh_manager = SSHProcessManager(self.config)
        self._build_ui()
        self._refresh_tree()
        self._start_polling()
        self.root.protocol("WM_DELETE_WINDOW", self._on_closing)

    def _build_ui(self):
        toolbar = ttk.Frame(self.root, padding=5)
        toolbar.pack(fill=tk.X)
        ttk.Button(toolbar, text="➕ 新建", command=self._new_session).pack(side=tk.LEFT, padx=1)
        ttk.Button(toolbar, text="✏️ 编辑", command=self._edit_session).pack(side=tk.LEFT, padx=1)
        ttk.Button(toolbar, text="📋 复制", command=self._duplicate_session).pack(side=tk.LEFT, padx=1)
        ttk.Button(toolbar, text="🗑️ 删除", command=self._delete_session).pack(side=tk.LEFT, padx=1)
        ttk.Button(toolbar, text="🔌 连接", command=self._connect_session).pack(side=tk.LEFT, padx=1)
        ttk.Button(toolbar, text="⛔ 断开", command=self._disconnect_session).pack(side=tk.LEFT, padx=1)
        ttk.Separator(toolbar, orient=tk.VERTICAL).pack(side=tk.LEFT, fill=tk.Y, padx=6)
        ttk.Separator(toolbar, orient=tk.VERTICAL).pack(side=tk.LEFT, fill=tk.Y, padx=4)
        ttk.Button(toolbar, text="🔗 全部连接", command=self._connect_all).pack(side=tk.LEFT, padx=1)
        ttk.Button(toolbar, text="⛔ 全部断开", command=self._disconnect_all).pack(side=tk.LEFT, padx=1)
        ttk.Button(toolbar, text="🔄 刷新", command=self._refresh_tree).pack(side=tk.LEFT, padx=1)
        ttk.Separator(toolbar, orient=tk.VERTICAL).pack(side=tk.LEFT, fill=tk.Y, padx=6)
        ttk.Button(toolbar, text="⚙️ 设置", command=self._show_settings).pack(side=tk.LEFT, padx=1)
        main_paned = ttk.PanedWindow(self.root, orient=tk.VERTICAL)
        main_paned.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)
        main_frame = ttk.Frame(main_paned)
        main_paned.add(main_frame, weight=3)
        ttk.Label(main_frame, text="Sessions", font=("Arial", 11, "bold")).pack(anchor=tk.W, padx=5, pady=5)
        columns = ("name", "host", "port", "username", "status", "forwarding_direction", "forwarding_local_port")
        self.tree = ttk.Treeview(main_frame, columns=columns, show="headings", height=20)
        for col in columns:
            hm = {"name": "Name", "host": "Host", "port": "Port", "username": "Username", "status": "Status", "forwarding_direction": "Direction", "forwarding_local_port": "Local Port"}
            self.tree.heading(col, text=hm[col])
            self.tree.column(col, width=100, anchor=tk.CENTER)
        self.tree.column("name", width=150, anchor=tk.W)
        self.tree.column("host", width=130, anchor=tk.W)
        self.tree.column("username", width=100, anchor=tk.W)
        self.tree.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)
        self.tree.bind("<Double-1>", lambda e: self._connect_session())
        self.tree.bind("<Button-3>", self._show_context_menu)
        log_frame = ttk.Frame(main_paned)
        main_paned.add(log_frame, weight=2)
        ttk.Label(log_frame, text="Connection Log", font=("Arial", 11, "bold")).pack(anchor=tk.W, padx=5, pady=5)
        self.log = LogWindow(log_frame)
        self.log.pack(fill=tk.BOTH, expand=True)
        self.status_var = tk.StringVar(value="Ready")
        status_bar = ttk.Frame(self.root, padding=3)
        status_bar.pack(fill=tk.X, side=tk.BOTTOM)
        self.clock_var = tk.StringVar(value="")
        ttk.Label(status_bar, textvariable=self.status_var, foreground="gray").pack(side=tk.LEFT)
        ttk.Label(status_bar, textvariable=self.clock_var, foreground="blue").pack(side=tk.RIGHT)

    def _refresh_tree(self):
        for item in self.tree.get_children():
            self.tree.delete(item)
        seen = set()
        for session in self.config.list_sessions():
            if session.name in seen:
                continue
            seen.add(session.name)
            status = chr(9679) + " Active" if session.name in self.ssh_manager.active_processes else chr(9675) + " Ready"
            if not session.enabled:
                status = chr(10005) + " Disabled"
            self.tree.insert("", tk.END, iid=session.name, values=(
                session.name, session.host, session.port, session.username, status,
                self._get_rule_field(session, "direction"), self._get_rule_field(session, "local_port")
            ), tags=("enabled" if session.enabled else "disabled"))

    def _get_rule_field(self, session, field):
        rules = getattr(session, "forward_rules", [])
        if rules:
            val = getattr(rules[0], field, "")
            return val if val else "-"
        return "-"

    def _new_session(self):
        session = SSHSession()
        dialog = SessionDialog(self.root, session)
        dialog.wait_window()
        if dialog.result:
            if not dialog.result.name.strip():
                messagebox.showwarning("Validation Error", "Session name cannot be empty.")
                return
            ok, err = self.config.add_session(dialog.result)
            if not ok:
                messagebox.showwarning("Port Conflict", err)
                return
            self._refresh_tree()
            self.log.append("Created session: " + dialog.result.name)

    def _edit_session(self):
        sel = self.tree.selection()
        if not sel:
            messagebox.showwarning("No Selection", "Please select a session to edit.")
            return
        name = sel[0]
        session = self.config.get_session(name)
        if session is None:
            return
        import copy
        dialog = SessionDialog(self.root, copy.deepcopy(session))
        dialog.wait_window()
        if dialog.result:
            if not dialog.result.name.strip():
                messagebox.showwarning("Validation Error", "Session name cannot be empty.")
                return
            ok, err = self.config.add_session(dialog.result)
            if not ok:
                messagebox.showwarning("Port Conflict", err)
                return
            self._refresh_tree()
            self.log.append("Edited session: " + dialog.result.name)

    def _duplicate_session(self):
        sel = self.tree.selection()
        if not sel:
            messagebox.showwarning("No Selection", "Please select a session to duplicate.")
            return
        name = sel[0]
        orig = self.config.get_session(name)
        if orig is None:
            return
        import copy
        new_session = copy.deepcopy(orig)
        new_session.name = orig.name + " (Copy)"
        dialog = SessionDialog(self.root, new_session)
        dialog.wait_window()
        if dialog.result:
            if not dialog.result.name.strip():
                messagebox.showwarning("Validation Error", "Session name cannot be empty.")
                return
            ok, err = self.config.add_session(dialog.result)
            if not ok:
                messagebox.showwarning("Port Conflict", err)
                return
            self._refresh_tree()
            self.log.append("Duplicated session: " + dialog.result.name)

    def _delete_session(self):
        sel = self.tree.selection()
        if not sel:
            messagebox.showwarning("No Selection", "Please select a session to delete.")
            return
        name = sel[0]
        if messagebox.askyesno("Confirm Delete", "Delete session " + repr(name) + "?"):
            self.config.remove_session(name)
            self._refresh_tree()
            self.log.append("Deleted session: " + name)

    def _connect_session(self):
        sel = self.tree.selection()
        if not sel:
            messagebox.showwarning("No Selection", "Please select a session to connect.")
            return
        name = sel[0]
        self.log.clear()
        self.log.append("")
        self.log.append("=" * 60)
        self.log.append("Connecting to: " + name)
        self.log.append("=" * 60)
        self.ssh_manager.start_session(name, self.log.append)
        self._refresh_tree()

    def _update_status_bar(self):
        """Recalculate and update the status bar display."""
        total = len(self.config.list_sessions())
        active_count = len(self.ssh_manager.active_processes)
        exited_count = total - active_count
        parts = [f"Total: {total}"]
        if active_count:
            parts.append(f"Active: {active_count}")
        if exited_count > 0:
            parts.append(f"Exited: {exited_count}")
        self.status_var.set(" | ".join(parts))

    def _disconnect_session(self):
        sel = self.tree.selection()
        if not sel:
            return
        name = sel[0]
        if name in self.ssh_manager.active_processes:
            self.ssh_manager.stop_session(name)
            self.log.append("Disconnected: " + name)
        else:
            self.log.append("Session " + repr(name) + " is not active.")
        self._refresh_tree()
        self._update_status_bar()

    def _connect_all(self):
        sessions = self.config.list_sessions()
        enabled = [s for s in sessions if s.enabled]
        if not enabled:
            messagebox.showwarning("No Sessions", "No sessions to connect.")
            return
        self.log.clear()
        self.log.append("")
        self.log.append("=" * 60)
        self.log.append("Connecting all (%d sessions)..." % len(enabled))
        self.log.append("=" * 60)
        for s in enabled:
            self.log.append("Connecting: %s" % s.name)
            self.ssh_manager.start_session(s.name, self.log.append)
        self._refresh_tree()

    def _disconnect_all(self):
        active = list(self.ssh_manager.active_processes.keys())
        if not active:
            messagebox.showinfo("Info", "No active sessions to disconnect.")
            return
        for name in active:
            try:
                self.ssh_manager.stop_session(name)
            except Exception as e:
                self.log.append("[Error] Failed to disconnect %s: %s" % (name, e))
                continue
            ts = datetime.now().strftime("%H:%M:%S")
            self.log.append("[%s] Disconnected: %s" % (ts, name))
        self._refresh_tree()
        self._update_status_bar()

    def _show_context_menu(self, event):
        sel = self.tree.selection()
        if not sel:
            return
        name = sel[0]
        menu = tk.Menu(self.root, tearoff=0)
        menu.add_command(label="🔌 连接", command=lambda: self._connect_session())
        menu.add_command(label="⛔ 断开", command=lambda: self._disconnect_session())
        menu.add_separator()
        menu.add_command(label="🔗 全部连接", command=lambda: self._connect_all())
        menu.add_command(label="⛔ 全部断开", command=lambda: self._disconnect_all())
        menu.add_separator()
        menu.add_command(label="✏️ 编辑", command=lambda: self._edit_session())
        menu.add_command(label="📋 复制", command=lambda: self._duplicate_session())
        menu.add_separator()
        menu.add_command(label="🗑️ 删除", command=lambda: self._delete_session())
        menu.tk_popup(event.x_root, event.y_root)

    def _show_settings(self):
        win = tk.Toplevel(self.root)
        win.title("Settings")
        win.geometry("500x350")
        win.transient(self.root)
        win.grab_set()
        ttk.Label(win, text="SSH Tunnel Manager Settings", font=("Arial", 12, "bold")).pack(pady=10)
        frame = ttk.Frame(win, padding=15)
        frame.pack(fill=tk.BOTH, expand=True)
        ttk.Label(frame, text="Config file location:").pack(anchor=tk.W, pady=3)
        ttk.Label(frame, text=self.config.config_path, foreground="gray", wraplength=450).pack(anchor=tk.W, pady=(0, 15))
        ttk.Label(frame, text="Active Sessions:").pack(anchor=tk.W, pady=3)
        active = list(self.ssh_manager.active_processes.keys())
        if active:
            ttk.Label(frame, text=", ".join(active), foreground="green").pack(anchor=tk.W, pady=(0, 15))
        else:
            ttk.Label(frame, text="None", foreground="gray").pack(anchor=tk.W, pady=(0, 15))
        ttk.Label(frame, text="Total Sessions:").pack(anchor=tk.W, pady=3)
        ttk.Label(frame, text=str(len(self.config.sessions))).pack(anchor=tk.W, pady=(0, 15))
        ttk.Label(frame, text="Version: 1.0").pack(anchor=tk.W, pady=3)
        ttk.Label(frame, text="Author: Doitky", foreground="blue").pack(anchor=tk.W, pady=(0, 15))
        ttk.Button(frame, text="Close", command=win.destroy).pack(pady=10)

    def _update_clock(self):
        """Update the clock display every second."""
        self.clock_var.set(datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
        self.root.after(1000, self._update_clock)

    def _start_polling(self):
        """Start the background polling thread and register the UI callback."""
        def _on_poll_status(name: str, status: str, detail: str):
            self.root.after(0, self._handle_poll_status, name, status, detail)
        self.ssh_manager.start_polling(callback=_on_poll_status, interval=2.0)
        self.log.append("[Polling] Connection health monitor started (interval: 2s)")
        self._update_clock()

    def _handle_poll_status(self, name: str, status: str, detail: str):
        """Handle polling results on the UI thread and refresh the tree."""
        total = len(self.config.list_sessions())
        active_count = len(self.ssh_manager.active_processes)
        exited_count = total - active_count
        parts = [f"Total: {total}"]
        if active_count:
            parts.append(f"Active: {active_count}")
        if exited_count > 0:
            parts.append(f"Exited: {exited_count}")
        self.status_var.set(" | ".join(parts))
        if status == "exited":
            self.log.append(f"[Polling] Session '{name}' disconnected: {detail}")
        self._refresh_tree()

    def _on_closing(self):
        self.ssh_manager.stop_polling()
        for name in list(self.ssh_manager.active_processes.keys()):
            self.ssh_manager.stop_session(name)
        self.root.destroy()


def main():
    root = tk.Tk()
    root.geometry("1100x700")
    style = ttk.Style()
    try:
        style.theme_use("clam")
    except tk.TclError:
        try:
            style.theme_use("vista")
        except tk.TclError:
            pass
    app = SSHTunnelManagerApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
