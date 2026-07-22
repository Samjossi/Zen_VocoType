"""T3.7 通知模块测试：命令构造、缺席降级、失败不抛。"""

import subprocess

import pytest

from zen_vocotype_launcher import notify
from zen_vocotype_launcher.notify import Notifier


class _Recorder:
    """command_runner 桩：记录调用，可配置抛错。"""

    def __init__(self, exc: Exception | None = None):
        self.calls: list[list[str]] = []
        self._exc = exc

    def __call__(self, cmd, **kwargs):
        self.calls.append(cmd)
        if self._exc is not None:
            raise self._exc


class TestNotifySend:
    def test_command_structure(self, monkeypatch):
        monkeypatch.setattr(notify.shutil, "which", lambda name: "/usr/bin/notify-send")
        rec = _Recorder()
        n = Notifier(command_runner=rec)
        n.notify_done(12.34)
        assert len(rec.calls) == 1
        cmd = rec.calls[0]
        assert cmd[0] == "/usr/bin/notify-send"
        assert "--urgency" in cmd
        # 图标经 icon_loader 传入（迁移图标存在时）
        if "--icon" in cmd:
            icon_arg = cmd[cmd.index("--icon") + 1]
            assert "zen_vocotype_launcher_icon" in icon_arg
        assert "Zen_VocoType" in cmd
        assert "12.3" in cmd[-1]

    def test_unavailable_degrades_to_log(self, monkeypatch):
        monkeypatch.setattr(notify.shutil, "which", lambda name: None)
        rec = _Recorder()
        n = Notifier(command_runner=rec)
        assert not n.available
        n.notify_starting()  # 降级仅日志，不调用 runner、不抛
        assert rec.calls == []

    def test_send_failure_not_raised(self, monkeypatch):
        monkeypatch.setattr(notify.shutil, "which", lambda name: "/usr/bin/notify-send")
        n = Notifier(command_runner=_Recorder(exc=OSError("boom")))
        n.notify_failed("服务端就绪", "/x/launcher.log")  # 记 warning 不抛

    def test_timeout_failure_not_raised(self, monkeypatch):
        monkeypatch.setattr(notify.shutil, "which", lambda name: "/usr/bin/notify-send")
        n = Notifier(command_runner=_Recorder(exc=subprocess.TimeoutExpired("notify-send", 3)))
        n.notify_starting()

    def test_three_kinds(self, monkeypatch):
        monkeypatch.setattr(notify.shutil, "which", lambda name: "/usr/bin/notify-send")
        rec = _Recorder()
        n = Notifier(command_runner=rec)
        n.notify_starting()
        n.notify_done(1.0)
        n.notify_failed("阶段X", "/log")
        bodies = [c[-1] for c in rec.calls]
        assert "正在启动" in bodies[0]
        assert "启动完成" in bodies[1]
        assert "启动失败" in bodies[2] and "阶段X" in bodies[2] and "/log" in bodies[2]
        # 失败通知为 critical 级别
        fail_cmd = rec.calls[2]
        assert fail_cmd[fail_cmd.index("--urgency") + 1] == "critical"
