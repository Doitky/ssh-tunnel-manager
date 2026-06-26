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
