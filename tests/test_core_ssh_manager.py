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
