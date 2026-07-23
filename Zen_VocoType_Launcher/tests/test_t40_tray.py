"""T40 单元测试：Launcher 托盘菜单结构 / 设置热切换 / 自动退出 / 回退与探针。

QT_QPA_PLATFORM 默认 offscreen（桌面环境不覆盖既有显示）；托盘零业务逻辑——
编排/持久化行为经 LauncherTrayApp 验证，外部动作全部 monkeypatch。
"""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtWidgets import QApplication

import zen_vocotype_launcher.app as app_mod
from zen_vocotype_launcher.app import (
    LauncherTrayApp,
    TrayUnavailableError,
    display_available,
)
from zen_vocotype_launcher.config import (
    CLIENT_START_INTERVAL_ENV_VAR,
    Settings,
)
from zen_vocotype_launcher.exit_codes import ExitCode
from zen_vocotype_launcher.tray import LauncherTray
from zen_vocotype_launcher.version import LAUNCHER_VERSION


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


# ---------------------------------------------------------------------- 托盘菜单结构


class TestTrayStructure:
    def test_title_first_and_version(self, qapp):
        tray = LauncherTray()
        assert tray._title_action.text() == f"Zen_VocoType Launcher v{LAUNCHER_VERSION}"
        assert not tray._title_action.isEnabled()

    def test_status_and_progress_lines(self, qapp):
        tray = LauncherTray()
        tray.set_status("Service：●运行中   Client：○未启动")
        assert "●运行中" in tray._status_action.text()
        tray.set_progress("将于 3 秒后启动服务端")
        assert tray._progress_action.isVisible()
        assert "3 秒后" in tray._progress_action.text()
        tray.set_progress(None)
        assert not tray._progress_action.isVisible()

    def test_busy_disables_start(self, qapp):
        tray = LauncherTray()
        tray.set_busy(True)
        assert not tray._start_action.isEnabled()
        tray.set_busy(False)
        assert tray._start_action.isEnabled()

    def test_delay_labels(self, qapp):
        tray = LauncherTray()
        tray.set_service_delay_label(3.0)
        tray.set_client_interval_label(2.5)
        tray.set_auto_exit_label(0.0)
        assert tray._service_delay_action.text() == "服务端启动延迟（3 秒）…"
        assert tray._client_interval_action.text() == "客户端启动间隔（2.5 秒）…"
        assert tray._auto_exit_action.text() == "成功后自动退出（0 秒）…"

    def test_binary_labels_and_reset_enablement(self, qapp):
        tray = LauncherTray()
        tray.set_service_binary_label(None)
        assert "未设置（自动）" in tray._service_binary_action.text()
        assert not tray._service_reset_action.isEnabled()
        tray.set_service_binary_label("/opt/Zen_VocoType_Service.AppImage")
        assert "Zen_VocoType_Service.AppImage" in tray._service_binary_action.text()
        assert tray._service_reset_action.isEnabled()

    def test_quit_caption_disclaims_components(self, qapp):
        tray = LauncherTray()
        assert "不影响已启动组件" in tray._quit_action.text()


# ---------------------------------------------------------------------- LauncherTrayApp 装配


@pytest.fixture()
def tray_app(qapp, monkeypatch):
    """LauncherTrayApp 实例（通知捕获替身；不进入事件循环）。"""
    notifications: list[tuple[str, str]] = []
    app = LauncherTrayApp(Settings(), __import__("pathlib").Path("launcher.log"))
    monkeypatch.setattr(
        app, "_notify", lambda title, msg: notifications.append((title, msg))
    )
    app.notifications = notifications
    return app


class TestTrayAppSettings:
    def test_init_labels_from_settings(self, qapp):
        app = LauncherTrayApp(
            Settings(
                service_start_delay_s=3,
                client_start_interval_s=5,
                auto_exit_delay_s=10,
            ),
            __import__("pathlib").Path("launcher.log"),
        )
        assert "3 秒" in app._tray._service_delay_action.text()
        assert "5 秒" in app._tray._client_interval_action.text()
        assert "10 秒" in app._tray._auto_exit_action.text()

    def test_apply_delay_success(self, tray_app, monkeypatch):
        written: list[tuple] = []
        monkeypatch.setattr(
            app_mod, "set_user_config_value", lambda k, v: written.append((k, v))
        )
        tray_app._apply_delay(
            "client_start_interval_s", 7, CLIENT_START_INTERVAL_ENV_VAR, "客户端启动间隔"
        )
        assert written == [("client_start_interval_s", 7)]
        assert tray_app._settings.client_start_interval_s == 7.0
        assert "7 秒" in tray_app._tray._client_interval_action.text()
        assert any("已更新为 7 秒" in msg for _, msg in tray_app.notifications)

    def test_apply_delay_persist_failure_no_effect(self, tray_app, monkeypatch):
        def boom(key, value):
            raise OSError("只读文件系统")

        monkeypatch.setattr(app_mod, "set_user_config_value", boom)
        tray_app._apply_delay(
            "client_start_interval_s", 7, CLIENT_START_INTERVAL_ENV_VAR, "客户端启动间隔"
        )
        assert tray_app._settings.client_start_interval_s == 0.0  # 内存未变
        assert "0 秒" in tray_app._tray._client_interval_action.text()
        assert any("写入失败" in msg for _, msg in tray_app.notifications)

    def test_apply_delay_env_override_warning(self, tray_app, monkeypatch):
        monkeypatch.setattr(app_mod, "set_user_config_value", lambda k, v: None)
        monkeypatch.setenv(CLIENT_START_INTERVAL_ENV_VAR, "9")
        tray_app._apply_delay(
            "client_start_interval_s", 7, CLIENT_START_INTERVAL_ENV_VAR, "客户端启动间隔"
        )
        assert any(
            CLIENT_START_INTERVAL_ENV_VAR in msg for _, msg in tray_app.notifications
        )

    def test_change_interval_dialog_accept(self, tray_app, monkeypatch):
        monkeypatch.setattr(app_mod, "set_user_config_value", lambda k, v: None)
        monkeypatch.setattr(
            "PySide6.QtWidgets.QInputDialog.getInt",
            staticmethod(lambda *a, **k: (12, True)),
        )
        tray_app._on_change_client_interval()
        assert tray_app._settings.client_start_interval_s == 12.0

    def test_change_interval_dialog_cancel(self, tray_app, monkeypatch):
        called = []
        monkeypatch.setattr(
            app_mod, "set_user_config_value", lambda k, v: called.append(1)
        )
        monkeypatch.setattr(
            "PySide6.QtWidgets.QInputDialog.getInt",
            staticmethod(lambda *a, **k: (0, False)),
        )
        tray_app._on_change_client_interval()
        assert called == []
        assert tray_app._settings.client_start_interval_s == 0.0


class TestBinarySettings:
    def test_apply_binary_set_and_reset(self, tray_app, monkeypatch, tmp_path):
        written: list[tuple] = []
        monkeypatch.setattr(
            app_mod, "set_user_config_value", lambda k, v: written.append((k, v))
        )
        binary = tmp_path / "Zen_VocoType_Service.AppImage"
        binary.touch()
        binary.chmod(0o755)
        tray_app._apply_binary("service_binary", str(binary), "ENV_X", "Service 位置")
        assert tray_app._settings.service_binary == str(binary)
        assert "Zen_VocoType_Service.AppImage" in tray_app._tray._service_binary_action.text()
        # 恢复自动解析：落盘 None、标签回「未设置（自动）」
        tray_app._apply_binary("service_binary", None, "ENV_X", "Service 位置")
        assert tray_app._settings.service_binary is None
        assert "未设置（自动）" in tray_app._tray._service_binary_action.text()
        assert written[-1] == ("service_binary", None)

    def test_pick_binary_rejects_non_executable(self, tray_app, monkeypatch, tmp_path):
        not_exec = tmp_path / "plain.txt"
        not_exec.touch()  # 无可执行位
        monkeypatch.setattr(
            "PySide6.QtWidgets.QFileDialog.getOpenFileName",
            staticmethod(lambda *a, **k: (str(not_exec), "")),
        )
        assert tray_app._pick_binary("选择") is None
        assert any("不可执行" in msg for _, msg in tray_app.notifications)

    def test_pick_binary_cancel(self, tray_app, monkeypatch):
        monkeypatch.setattr(
            "PySide6.QtWidgets.QFileDialog.getOpenFileName",
            staticmethod(lambda *a, **k: ("", "")),
        )
        assert tray_app._pick_binary("选择") is None


# ---------------------------------------------------------------------- 自动退出 / 失败停留


class TestAutoExit:
    def test_success_zero_delay_quits_immediately(self, tray_app, monkeypatch):
        quit_called = []
        monkeypatch.setattr(
            tray_app._qapp, "quit", lambda: quit_called.append(1)
        )
        tray_app._settings.auto_exit_delay_s = 0.0
        tray_app._on_finished(int(ExitCode.SUCCESS))
        assert quit_called == [1]

    def test_success_positive_delay_countdown_then_quit(self, tray_app, monkeypatch):
        quit_called = []
        monkeypatch.setattr(
            tray_app._qapp, "quit", lambda: quit_called.append(1)
        )
        tray_app._settings.auto_exit_delay_s = 3.0
        tray_app._on_finished(int(ExitCode.SUCCESS))
        assert quit_called == []  # 倒计时中，未退出
        assert "3 秒后退出" in tray_app._tray._progress_action.text()
        tray_app._on_auto_exit_tick()
        tray_app._on_auto_exit_tick()
        assert quit_called == []
        tray_app._on_auto_exit_tick()
        assert quit_called == [1]

    def test_failure_stays_no_auto_exit(self, tray_app, monkeypatch):
        quit_called = []
        monkeypatch.setattr(
            tray_app._qapp, "quit", lambda: quit_called.append(1)
        )
        tray_app._on_finished(int(ExitCode.SERVICE_FAILED))
        assert quit_called == []  # 🔴 失败路径不自动退出
        assert "启动失败" in tray_app._tray._progress_action.text()
        assert not tray_app._auto_exit_timer.isActive()

    def test_retry_cancels_pending_auto_exit(self, tray_app, monkeypatch):
        """重试（立即启动）撤销待定退出倒计时。"""
        quit_called = []
        monkeypatch.setattr(
            tray_app._qapp, "quit", lambda: quit_called.append(1)
        )
        tray_app._settings.auto_exit_delay_s = 5.0
        tray_app._on_finished(int(ExitCode.SUCCESS))
        assert tray_app._auto_exit_timer.isActive()
        # 模拟重试：_on_start 内 _stop_auto_exit（busy 复位后）
        monkeypatch.setattr(tray_app, "_QThread", _FakeThread)
        tray_app._on_start()
        assert not tray_app._auto_exit_timer.isActive()


class _FakeThread:
    """QThread 替身：不发线程，同步跳过（仅验证 _stop_auto_exit 接线）。"""

    def __init__(self, parent=None):
        self.started = _FakeSignal()
        self.finished = _FakeSignal()

    def start(self):
        pass

    def deleteLater(self):
        pass


class _FakeSignal:
    def connect(self, slot):
        pass


# ---------------------------------------------------------------------- 状态检测 / 忙碌守卫


class TestStatusAndBusy:
    def test_refresh_status_resolution_failure_visible(self, tray_app, monkeypatch, tmp_path):
        """目标解析失败：状态行红字错误 + 位置补救提示（痛点一闭环）。"""
        # 测试进程 sys.argv[0] 邻接无二进制 + 兜底目录置空（隔离宿主 ~/AppImages）
        empty = tmp_path / "empty"
        empty.mkdir()
        monkeypatch.setattr(
            "zen_vocotype_launcher.targets.FALLBACK_SEARCH_DIRS", (empty,)
        )
        tray_app.refresh_status()
        text = tray_app._tray._status_action.text()
        assert "✗" in text and "位置" in text

    def test_refresh_status_running(self, tray_app, monkeypatch):
        from zen_vocotype_launcher.discovery import ComponentStatus, DiscoveryResult

        monkeypatch.setattr(
            app_mod,
            "build_plan",
            lambda settings, dev_mode: type(
                "P",
                (),
                {
                    "service": type("T", (), {"expected_exe": "/s"})(),
                    "client": type("T", (), {"expected_exe": "/c"})(),
                },
            )(),
        )
        monkeypatch.setattr(
            app_mod,
            "discover_component",
            lambda lock_path, name, expected_exe=None: DiscoveryResult(
                ComponentStatus.RUNNING, pid=4321
            ),
        )
        tray_app.refresh_status()
        text = tray_app._tray._status_action.text()
        assert "●运行中" in text and "4321" in text

    def test_busy_guard_ignores_reentry(self, tray_app, monkeypatch):
        tray_app._busy = True
        monkeypatch.setattr(
            tray_app,
            "_QThread",
            lambda *a: pytest.fail("忙碌中不应创建新线程"),
        )
        tray_app._on_start()  # 直接返回，不触发 _QThread


# ---------------------------------------------------------------------- 回退与探针


class TestFallbackAndProbe:
    def test_display_unavailable_raises(self, qapp, monkeypatch):
        monkeypatch.delenv("DISPLAY", raising=False)
        monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
        assert not display_available()
        with pytest.raises(TrayUnavailableError):
            LauncherTrayApp(Settings(), __import__("pathlib").Path("launcher.log"))

    def test_display_available_with_env(self, monkeypatch):
        monkeypatch.setenv("DISPLAY", ":0")
        assert display_available()

    def test_version_probe_no_tray_import(self, monkeypatch):
        """--version 探针：不触达托盘模块/PySide6（零写盘回归）。"""
        import subprocess
        import sys

        repo = __import__("pathlib").Path(__file__).resolve().parents[2]
        env = os.environ.copy()
        env["PYTHONPATH"] = os.pathsep.join(
            [
                str(repo / "Zen_VocoType_Launcher" / "src"),
                str(repo / "Zen_VocoType_Protocol" / "src"),
            ]
        )
        proc = subprocess.run(
            [
                sys.executable,
                "-c",
                "import sys; sys.argv=['launcher','--version']; "
                "from main import main; rc = main(); "
                "assert 'zen_vocotype_launcher.app' not in sys.modules, '探针触达托盘模块'; "
                "assert 'PySide6' not in sys.modules, '探针触达 PySide6'; "
                "sys.exit(rc)",
            ],
            cwd=repo / "Zen_VocoType_Launcher",
            env=env,
            capture_output=True,
            text=True,
            timeout=60,
        )
        assert proc.returncode == 0, proc.stderr
        assert "v1." in proc.stdout + proc.stderr
