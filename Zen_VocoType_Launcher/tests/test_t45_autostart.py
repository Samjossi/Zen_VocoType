"""T45 单元测试：Launcher 登录自启动（XDG Autostart）。

核心模块（``autostart.py``）为纯文件测试，经 monkeypatch ``XDG_CONFIG_HOME``
指向 ``tmp_path`` 隔离宿主；装配层沿用 ``test_t40_tray.py`` 既有模式
（offscreen、通知捕获替身、外部动作全部 monkeypatch）。
"""

import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtWidgets import QApplication

import zen_vocotype_launcher.app as app_mod
from zen_vocotype_launcher.app import LauncherTrayApp
from zen_vocotype_launcher.autostart import AutostartManager
from zen_vocotype_launcher.config import AUTOSTART_ENV_VAR, Settings
from zen_vocotype_launcher.tray import LauncherTray


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


@pytest.fixture()
def mgr(monkeypatch, tmp_path):
    """autostart 目录隔离到 tmp_path 的 AutostartManager。"""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    return AutostartManager()


# ---------------------------------------------------------------------- 核心模块：三态读取


class TestIsEnabled:
    def test_file_missing(self, mgr):
        assert mgr.is_enabled() is False

    def test_hidden_true(self, mgr):
        mgr.desktop_path.parent.mkdir(parents=True)
        mgr.desktop_path.write_text("[Desktop Entry]\nHidden=true\n", encoding="utf-8")
        assert mgr.is_enabled() is False

    def test_hidden_false(self, mgr):
        mgr.desktop_path.parent.mkdir(parents=True)
        mgr.desktop_path.write_text("[Desktop Entry]\nHidden=false\n", encoding="utf-8")
        assert mgr.is_enabled() is True

    def test_no_hidden_line_defaults_enabled(self, mgr):
        mgr.desktop_path.parent.mkdir(parents=True)
        mgr.desktop_path.write_text("[Desktop Entry]\nName=X\n", encoding="utf-8")
        assert mgr.is_enabled() is True


# ---------------------------------------------------------------------- 核心模块：写入与原子性


class TestSetEnabled:
    def test_enable_creates_from_template(self, mgr):
        assert mgr.set_enabled(True) is True
        text = mgr.desktop_path.read_text(encoding="utf-8")
        assert text.startswith("[Desktop Entry]\n")
        assert "Name=Zen_VocoType Launcher\n" in text
        assert "GenericName=Voice Input Launcher\n" in text
        assert "Hidden=false\n" in text
        assert "X-GNOME-Autostart-enabled=true\n" in text
        assert "\nExec=" in text

    def test_disable_preserves_custom_lines(self, mgr):
        mgr.set_enabled(True)
        # 用户自定义行注入后再禁用：自定义行保留，Hidden 翻转为 true
        text = mgr.desktop_path.read_text(encoding="utf-8")
        mgr.desktop_path.write_text(text + "Comment=用户自定义\n", encoding="utf-8")
        assert mgr.set_enabled(False) is True
        text = mgr.desktop_path.read_text(encoding="utf-8")
        assert "Comment=用户自定义\n" in text
        assert "Hidden=true\n" in text
        assert "Hidden=false" not in text
        assert mgr.is_enabled() is False

    def test_exec_line_replaced_on_rewrite(self, mgr, monkeypatch, tmp_path):
        mgr.set_enabled(True)
        fake_appimage = tmp_path / "zen_vocotype_launcher.appimage"
        fake_appimage.touch()
        monkeypatch.setenv("APPIMAGE", str(fake_appimage))
        # 状态未变时不重写；翻转一次触发重写，Exec 应更新为当前形态
        mgr.set_enabled(False)
        mgr.set_enabled(True)
        text = mgr.desktop_path.read_text(encoding="utf-8")
        exec_lines = [l for l in text.splitlines() if l.startswith("Exec=")]
        assert exec_lines == [f'Exec="{fake_appimage}"']

    def test_no_temp_file_residue(self, mgr):
        mgr.set_enabled(True)
        mgr.set_enabled(False)
        assert not mgr.desktop_path.with_suffix(".desktop.new").exists()
        assert list(mgr.desktop_path.parent.glob("*.new")) == []

    def test_set_same_state_is_noop(self, mgr):
        assert mgr.set_enabled(False) is True  # 未启用 → 禁用：无文件创建
        assert not mgr.desktop_path.exists()


# ---------------------------------------------------------------------- 核心模块：遗留清理


class TestLegacyCleanup:
    def test_legacy_file_removed_and_returned(self, mgr):
        legacy = mgr.desktop_path.parent / "zen_vocotype.desktop"
        mgr.desktop_path.parent.mkdir(parents=True)
        legacy.write_text("[Desktop Entry]\n", encoding="utf-8")
        removed = mgr.remove_legacy_desktop_files()
        assert removed == [legacy]
        assert not legacy.exists()

    def test_no_legacy_returns_empty(self, mgr):
        assert mgr.remove_legacy_desktop_files() == []

    def test_main_desktop_file_untouched(self, mgr):
        mgr.set_enabled(True)
        mgr.remove_legacy_desktop_files()
        assert mgr.desktop_path.exists()
        assert mgr.is_enabled() is True

    def test_idempotent(self, mgr):
        legacy = mgr.desktop_path.parent / "zen_vocotype.desktop"
        mgr.desktop_path.parent.mkdir(parents=True)
        legacy.write_text("[Desktop Entry]\n", encoding="utf-8")
        assert len(mgr.remove_legacy_desktop_files()) == 1
        assert mgr.remove_legacy_desktop_files() == []


# ---------------------------------------------------------------------- 核心模块：平台与 Exec 三档


class TestPlatformAndExec:
    def test_is_supported_linux(self, monkeypatch):
        monkeypatch.setattr(sys, "platform", "linux")
        assert AutostartManager.is_supported() is True

    def test_is_supported_non_linux(self, monkeypatch):
        monkeypatch.setattr(sys, "platform", "darwin")
        assert AutostartManager.is_supported() is False

    def test_exec_appimage_tier(self, mgr, monkeypatch, tmp_path):
        fake_appimage = tmp_path / "zen_vocotype_launcher.appimage"
        fake_appimage.touch()
        monkeypatch.setenv("APPIMAGE", str(fake_appimage))
        assert mgr._get_exec_cmd() == f'"{fake_appimage}"'

    def test_exec_frozen_tier(self, mgr, monkeypatch):
        monkeypatch.delenv("APPIMAGE", raising=False)
        monkeypatch.setattr(sys, "frozen", True, raising=False)
        assert mgr._get_exec_cmd() == f'"{sys.executable}"'

    def test_exec_dev_tier(self, mgr, monkeypatch):
        monkeypatch.delenv("APPIMAGE", raising=False)
        cmd = mgr._get_exec_cmd()
        assert cmd.startswith(f'"{sys.executable}" "')
        assert cmd.endswith('main.py"')


# ---------------------------------------------------------------------- 托盘菜单项（零业务逻辑）


class TestTrayAutostartItem:
    def test_checkable_item_exists(self, qapp):
        tray = LauncherTray()
        assert tray._autostart_action.isCheckable()
        assert "登录后自动启动" in tray._autostart_action.text()

    def test_trigger_emits_signal_with_checked(self, qapp):
        tray = LauncherTray()
        received: list[bool] = []
        tray.autostart_change_requested.connect(received.append)
        tray._autostart_action.trigger()
        assert received == [True]
        tray._autostart_action.trigger()
        assert received == [True, False]

    def test_set_checked_blocks_signal(self, qapp):
        """初始化/回滚刷新勾选态不得重入 triggered（blockSignals）。"""
        tray = LauncherTray()
        received: list[bool] = []
        tray.autostart_change_requested.connect(received.append)
        tray.set_autostart_checked(True)
        assert tray._autostart_action.isChecked() is True
        assert received == []

    def test_unsupported_disables_and_annotates(self, qapp):
        tray = LauncherTray()
        tray.set_autostart_supported(False)
        assert not tray._autostart_action.isEnabled()
        assert "仅 Linux 支持" in tray._autostart_action.text()


# ---------------------------------------------------------------------- 装配层


@pytest.fixture()
def tray_app(qapp, monkeypatch, tmp_path):
    """LauncherTrayApp 实例（autostart 目录隔离 tmp_path；通知捕获替身）。"""
    monkeypatch.setenv("DISPLAY", os.environ.get("DISPLAY", ":0"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    notifications: list[tuple[str, str]] = []
    app = LauncherTrayApp(Settings(), __import__("pathlib").Path("launcher.log"))
    monkeypatch.setattr(
        app, "_notify", lambda title, msg: notifications.append((title, msg))
    )
    app.notifications = notifications
    return app


class TestTrayAppAutostart:
    def test_initial_unchecked_by_default(self, tray_app):
        assert tray_app._tray._autostart_action.isChecked() is False
        assert tray_app._tray._autostart_action.isEnabled() is True
        # 配置未启用且无 desktop 文件：一致性校验为 noop，不创建文件
        assert not tray_app._autostart_mgr.desktop_path.exists()

    def test_initial_checked_from_config(self, qapp, monkeypatch, tmp_path):
        """配置启用但 desktop 文件缺失：启动一致性校验按配置重建并勾选。"""
        monkeypatch.setenv("DISPLAY", os.environ.get("DISPLAY", ":0"))
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
        app = LauncherTrayApp(
            Settings(autostart_enabled=True),
            __import__("pathlib").Path("launcher.log"),
        )
        assert app._tray._autostart_action.isChecked() is True
        assert app._autostart_mgr.is_enabled() is True

    def test_consistency_disables_stale_file(self, qapp, monkeypatch, tmp_path):
        """配置未启用但 desktop 文件处于启用态：按配置修复为禁用。"""
        monkeypatch.setenv("DISPLAY", os.environ.get("DISPLAY", ":0"))
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
        mgr = AutostartManager()
        mgr.set_enabled(True)
        app = LauncherTrayApp(Settings(), __import__("pathlib").Path("launcher.log"))
        assert app._autostart_mgr.is_enabled() is False
        assert app._tray._autostart_action.isChecked() is False

    def test_unsupported_platform_disables_item(self, qapp, monkeypatch, tmp_path):
        monkeypatch.setenv("DISPLAY", os.environ.get("DISPLAY", ":0"))
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
        monkeypatch.setattr(app_mod.AutostartManager, "is_supported", lambda: False)
        app = LauncherTrayApp(Settings(), __import__("pathlib").Path("launcher.log"))
        assert not app._tray._autostart_action.isEnabled()
        assert "仅 Linux 支持" in app._tray._autostart_action.text()

    def test_toggle_persists_and_writes_desktop(self, tray_app, monkeypatch):
        written: list[tuple] = []
        monkeypatch.setattr(
            app_mod, "set_user_config_value", lambda k, v: written.append((k, v))
        )
        tray_app._tray._autostart_action.trigger()
        assert written == [("autostart_enabled", True)]
        assert tray_app._settings.autostart_enabled is True
        assert tray_app._autostart_mgr.is_enabled() is True
        assert any("设置已更新" in t for t, _ in tray_app.notifications)
        # 再触发一次：禁用后文件保留、Hidden=true
        tray_app._tray._autostart_action.trigger()
        assert written[-1] == ("autostart_enabled", False)
        assert tray_app._settings.autostart_enabled is False
        assert tray_app._autostart_mgr.desktop_path.exists()
        assert tray_app._autostart_mgr.is_enabled() is False

    def test_env_override_warning_suffix(self, tray_app, monkeypatch):
        monkeypatch.setattr(app_mod, "set_user_config_value", lambda k, v: None)
        monkeypatch.setenv(AUTOSTART_ENV_VAR, "1")
        tray_app._tray._autostart_action.trigger()
        assert any(
            AUTOSTART_ENV_VAR in msg for _, msg in tray_app.notifications
        )

    def test_persist_failure_rolls_back_check(self, tray_app, monkeypatch):
        """落盘失败：勾选态回滚、内存值不变、desktop 文件不动。"""
        def boom(key, value):
            raise OSError("只读文件系统")

        monkeypatch.setattr(app_mod, "set_user_config_value", boom)
        tray_app._tray._autostart_action.trigger()
        assert tray_app._tray._autostart_action.isChecked() is False
        assert tray_app._settings.autostart_enabled is False
        assert not tray_app._autostart_mgr.desktop_path.exists()
        assert any("设置未生效" in t for t, _ in tray_app.notifications)

    def test_desktop_write_failure_rolls_back_both(self, tray_app, monkeypatch):
        """desktop 写入失败：勾选态与配置双回滚（🔴 两端不得分叉）。"""
        written: list[tuple] = []
        monkeypatch.setattr(
            app_mod, "set_user_config_value", lambda k, v: written.append((k, v))
        )
        monkeypatch.setattr(tray_app._autostart_mgr, "set_enabled", lambda e: False)
        tray_app._tray._autostart_action.trigger()
        assert written == [("autostart_enabled", True), ("autostart_enabled", False)]
        assert tray_app._tray._autostart_action.isChecked() is False
        assert tray_app._settings.autostart_enabled is False
        assert any("设置未生效" in t for t, _ in tray_app.notifications)

    def test_startup_cleans_legacy_entry(self, qapp, monkeypatch, tmp_path):
        """启动装配：预置下划线旧名遗留条目被清理，本体按配置状态维护。"""
        monkeypatch.setenv("DISPLAY", os.environ.get("DISPLAY", ":0"))
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
        autostart_dir = tmp_path / "autostart"
        autostart_dir.mkdir(parents=True)
        legacy = autostart_dir / "zen_vocotype.desktop"
        legacy.write_text("[Desktop Entry]\n", encoding="utf-8")
        LauncherTrayApp(Settings(), __import__("pathlib").Path("launcher.log"))
        assert not legacy.exists()
