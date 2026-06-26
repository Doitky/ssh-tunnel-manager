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
