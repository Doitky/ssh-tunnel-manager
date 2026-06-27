import threading
import time
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


def test_check_local_ports_in_use_free(mgr, tmp_path):
    """空闲端口不应被报告为占用。"""
    from core.models import SSHSession, PortForwardRule
    # 取一个系统分配的空闲端口用于测试
    import socket as _sock
    s = _sock.socket()
    s.bind(("127.0.0.1", 0))
    free_port = s.getsockname()[1]
    s.close()
    sess = SSHSession(name="t", host="h", username="u", auth_method="key",
                      key_path=str(tmp_path / "k"),
                      forward_rules=[PortForwardRule(direction="local", local_port=free_port,
                                                     remote_host="127.0.0.1", remote_port=80)])
    assert mgr.check_local_ports_in_use(sess) == []


def test_check_local_ports_in_use_occupied(mgr, tmp_path):
    """被占用的端口应被报告。"""
    from core.models import SSHSession, PortForwardRule
    import socket as _sock
    # 占住一个端口
    holder = _sock.socket()
    holder.bind(("127.0.0.1", 0))
    held_port = holder.getsockname()[1]
    holder.listen(1)
    try:
        sess = SSHSession(name="t", host="h", username="u", auth_method="key",
                          key_path=str(tmp_path / "k"),
                          forward_rules=[PortForwardRule(direction="local", local_port=held_port,
                                                         remote_host="127.0.0.1", remote_port=80)])
        busy = mgr.check_local_ports_in_use(sess)
        assert held_port in busy
    finally:
        holder.close()


def test_check_local_ports_ignores_remote_direction(mgr, tmp_path):
    """remote 方向端口在远程监听，本地不应检测。"""
    from core.models import SSHSession, PortForwardRule
    import socket as _sock
    holder = _sock.socket()
    holder.bind(("127.0.0.1", 0))
    held_port = holder.getsockname()[1]
    holder.listen(1)
    try:
        sess = SSHSession(name="t", host="h", username="u", auth_method="key",
                          key_path=str(tmp_path / "k"),
                          forward_rules=[PortForwardRule(direction="remote", local_port=held_port,
                                                         remote_host="127.0.0.1", remote_port=80)])
        # remote 方向即便本地占用该端口，也不应报告（因为本地不监听）
        assert mgr.check_local_ports_in_use(sess) == []
    finally:
        holder.close()


def test_stop_session_marks_stop_requested(mgr, tmp_path, monkeypatch):
    """用户主动断开应置 _stop_requested，重连逻辑据此跳过。"""
    from core.models import SSHSession, PortForwardRule
    sess = SSHSession(name="t", host="h", username="u", auth_method="key",
                      key_path=str(tmp_path / "k"), auto_reconnect=True,
                      reconnect_interval=1,
                      forward_rules=[PortForwardRule(direction="local", local_port=0,
                                                     remote_host="127.0.0.1", remote_port=80)])
    mgr.config.add_session(sess)

    started = threading.Event()
    calls = {"count": 0}

    class FakeProc:
        def __init__(self):
            self._rc = None
            self.stdout = iter([])  # 无输出
        def poll(self):
            return self._rc
        def wait(self, timeout=None):
            return self._rc
        @property
        def returncode(self):
            return self._rc
        def terminate(self):
            self._rc = 0
        def kill(self):
            self._rc = 0
        @property
        def stdin(self):
            class _S:
                def write(self, *a): pass
                def flush(self): pass
            return _S()

    def fake_popen(cmd, **kw):
        calls["count"] += 1
        started.set()
        return FakeProc()

    monkeypatch.setattr("core.ssh_manager.subprocess.Popen", fake_popen)

    mgr.start_session("t")
    assert started.wait(2), "进程未启动"
    # 主动停止
    mgr.stop_session("t")
    info = mgr.active_processes.get("t")
    # 主动停止后标记应已置位（在 stop 时即设置）
    # 等待 _run 线程退出
    time.sleep(1.5)
    # 不应发生重连：popen 只调用一次
    assert calls["count"] == 1, f"主动断开不应重连，但 popen 调用了 {calls['count']} 次"


def test_auto_reconnect_retries_on_exit(mgr, tmp_path, monkeypatch):
    """开启自动重连且进程退出（非主动停止）应再次拉起进程。"""
    from core.models import SSHSession, PortForwardRule
    sess = SSHSession(name="t2", host="h", username="u", auth_method="key",
                      key_path=str(tmp_path / "k"), auto_reconnect=True,
                      reconnect_interval=1,
                      forward_rules=[PortForwardRule(direction="local", local_port=0,
                                                     remote_host="127.0.0.1", remote_port=80)])
    mgr.config.add_session(sess)

    calls = {"count": 0}
    started = threading.Event()

    class FakeProc:
        def __init__(self):
            self._rc = None
            self.stdout = iter([])
        def poll(self):
            return self._rc
        def wait(self, timeout=None):
            return self._rc
        @property
        def returncode(self):
            return self._rc
        def terminate(self):
            self._rc = 0
        def kill(self):
            self._rc = 0
        @property
        def stdin(self):
            class _S:
                def write(self, *a): pass
                def flush(self): pass
            return _S()

    def fake_popen(cmd, **kw):
        calls["count"] += 1
        started.set()
        return FakeProc()

    monkeypatch.setattr("core.ssh_manager.subprocess.Popen", fake_popen)

    mgr.start_session("t2")
    assert started.wait(2)
    # 给重连间隔（1s）+余量，等待第二次拉起
    time.sleep(2.5)
    # 应至少重连一次（popen 调用 >= 2）
    assert calls["count"] >= 2, f"应自动重连，但 popen 只调用了 {calls['count']} 次"
    # 清理：主动停止
    info = mgr.active_processes.get("t2")
    if info:
        info["_stop_requested"] = True
