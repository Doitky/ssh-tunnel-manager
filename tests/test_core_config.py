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
