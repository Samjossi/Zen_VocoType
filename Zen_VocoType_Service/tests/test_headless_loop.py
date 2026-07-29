"""T42 headless 主循环收敛单测（S3，计划 2026-0730-0221）。

覆盖：

- 共用轮询循环 ``_poll_shutdown_event``：事件置位后循环退出（in-process）
- ``_run_headless`` 真实子进程：``QCoreApplication`` 可创建（offscreen/
  无 DISPLAY）、watcher 接入不崩、置位 event 后循环退出

🔴 offscreen 平台必须在 PySide6 导入前设置（headless CI 兼容）。
"""

import os
import subprocess
import sys
import threading
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtWidgets import QApplication

from zen_vocotype_service.config import COMPONENT_ROOT


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


@pytest.fixture()
def main_module():
    """导入 Service main 模块（镜像 test_tray.py TestMainDegradation 模式）。"""
    if str(COMPONENT_ROOT) not in sys.path:
        sys.path.insert(0, str(COMPONENT_ROOT))
    import main

    return main


class TestPollShutdownEvent:
    def test_loop_exits_when_event_set(self, qapp, main_module):
        """事件已置位 → 轮询 watchdog 在 200ms 量级内驱动 app.quit()，exec 返回。"""
        event = threading.Event()
        event.set()
        # exec 阻塞直至 watchdog 触发 quit；超时即测试卡死（由 pytest 超时兜底）
        main_module._poll_shutdown_event(qapp, event)

    def test_loop_exits_when_event_set_later(self, qapp, main_module):
        """事件后置位（模拟 SIGTERM 到达）→ 循环随后退出。"""
        event = threading.Event()
        threading.Timer(0.3, event.set).start()
        main_module._poll_shutdown_event(qapp, event)
        assert event.is_set()


class TestRunHeadlessSubprocess:
    def test_headless_loop_real_qt(self):
        """真实子进程：QCoreApplication 创建 + watcher 接入 + 事件置位退出。

        子进程隔离避免与套件内既有 QApplication 单例冲突；watcher 在总线
        不可达环境下静默降级、可达则正常订阅——两种结局均不得崩溃。
        """
        script = (
            "import threading\n"
            "import main\n"
            "event = threading.Event()\n"
            "threading.Timer(0.5, event.set).start()\n"
            "main._run_headless(event)\n"
            "print('LOOP_EXITED', flush=True)\n"
        )
        env = dict(os.environ)
        env["QT_QPA_PLATFORM"] = "offscreen"
        env.pop("DISPLAY", None)
        env.pop("WAYLAND_DISPLAY", None)
        proc = subprocess.run(
            [sys.executable, "-c", script],
            cwd=str(COMPONENT_ROOT),
            env=env,
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert proc.returncode == 0, (
            f"headless 主循环子进程异常退出: rc={proc.returncode}\n"
            f"stdout: {proc.stdout}\nstderr: {proc.stderr}"
        )
        assert "LOOP_EXITED" in proc.stdout
