import json
import os
from datetime import datetime
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
