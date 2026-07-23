"""T41 Client DISPLAY 探测单测（2026-07-23 systemd 无显示环境实机事故）。

三组件对齐：Service（main.py:101）/Launcher（app.py:72）已有探测，Client
补齐——无显示环境下 Qt 会 SIGABRT 硬崩（非 Python 异常，无法捕获降级），
探测必须先于单实例锁与 QApplication（早失败、不触锁、不触 Qt）。
"""

import sys

import pytest

from zen_vocotype_client.config import COMPONENT_ROOT


@pytest.fixture()
def main_module():
    """导入 Client main 模块（镜像 Service test_tray.py TestMainDegradation 模式）。"""
    if str(COMPONENT_ROOT) not in sys.path:
        sys.path.insert(0, str(COMPONENT_ROOT))
    import main

    return main


class TestDisplayAvailable:
    def test_no_display_returns_false(self, main_module, monkeypatch):
        """无 DISPLAY/WAYLAND_DISPLAY → False（headless 判定）。"""
        monkeypatch.delenv("DISPLAY", raising=False)
        monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
        assert main_module.display_available() is False

    def test_display_set_returns_true(self, main_module, monkeypatch):
        monkeypatch.setenv("DISPLAY", ":0")
        assert main_module.display_available() is True

    def test_wayland_set_returns_true(self, main_module, monkeypatch):
        monkeypatch.delenv("DISPLAY", raising=False)
        monkeypatch.setenv("WAYLAND_DISPLAY", "wayland-0")
        assert main_module.display_available() is True


class TestMainHeadlessGuard:
    def test_headless_exits_6_without_touching_qt_or_lock(
        self, main_module, monkeypatch
    ):
        """无显示环境 → 退出码 6；🔴 探测先于 Qt 与单实例锁（顺序固化）。"""
        monkeypatch.delenv("DISPLAY", raising=False)
        monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
        monkeypatch.setattr(sys, "argv", ["main.py"])

        # Qt 触达即失败（探测必须先于 QApplication import/创建）
        import PySide6.QtWidgets

        def _boom(*args, **kwargs):
            raise AssertionError("headless 下触达 QApplication——探测顺序被破坏")

        monkeypatch.setattr(PySide6.QtWidgets, "QApplication", _boom)

        # 单实例锁触达即失败（探测必须先于抢锁，不得留锁文件副作用）
        from zen_vocotype_client import instance_lock

        class _LockBoom:
            def __init__(self, *args, **kwargs):
                raise AssertionError("headless 下触达 InstanceLock——探测顺序被破坏")

        monkeypatch.setattr(instance_lock, "InstanceLock", _LockBoom)

        assert main_module.main() == 6

    def test_display_present_not_blocked_by_probe(self, main_module, monkeypatch):
        """有显示环境时探测不拦截（装配后续由既有 e2e 覆盖，此处只验证
        探测本身放行——用 InstanceLock 触达作为「已越过探测点」的探针）。

        沿用会话环境 DISPLAY（套件既有假设：pynput 可导入，validate_startup
        经 hotkey.combo 触达 pynput），不另行设置。"""
        monkeypatch.setattr(sys, "argv", ["main.py"])

        from zen_vocotype_client import instance_lock

        reached = []

        class _Probe:
            def __init__(self, *args, **kwargs):
                reached.append(True)
                raise RuntimeError("探针止步：越过探测点即通过")

        monkeypatch.setattr(instance_lock, "InstanceLock", _Probe)

        with pytest.raises(RuntimeError, match="探针止步"):
            main_module.main()
        assert reached == [True]
