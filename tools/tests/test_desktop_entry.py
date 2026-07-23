"""T43 desktop_entry autostart 安装支持单测。

hermetic 设计：XDG_DATA_HOME / XDG_CONFIG_HOME 均 monkeypatch 到 tmp_path
（零真实用户目录副作用）；图标提取（需真实 AppImage）与桌面数据库刷新
（外部命令）以 fake 隔离——图标链路已有阶段 4 实机验收覆盖，本文件聚焦
条目渲染/安装/卸载对称逻辑。
"""

import sys
from pathlib import Path

import pytest

TOOLS_DIR = Path(__file__).resolve().parents[1]
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

import desktop_entry  # noqa: E402


@pytest.fixture()
def xdg_env(tmp_path, monkeypatch):
    """隔离 XDG 目录 + fake 图标提取/外部命令；返回 (app_dir, data_home, cfg_home)。"""
    data_home = tmp_path / "data"
    cfg_home = tmp_path / "config"
    monkeypatch.setenv("XDG_DATA_HOME", str(data_home))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(cfg_home))
    # 外部刷新命令（update-desktop-database / gtk-update-icon-cache）视为缺席
    monkeypatch.setattr(desktop_entry.shutil, "which", lambda cmd: None)

    app_dir = tmp_path / "apps"
    app_dir.mkdir()
    (app_dir / "Zen_VocoType_Launcher.AppImage").touch()

    # fake 图标提取：产出四档假图标文件（避免真实 AppImage 依赖）
    def fake_extract(launcher, staging):
        staging.mkdir(parents=True, exist_ok=True)
        icons = {}
        for size in desktop_entry.ICON_SIZES:
            icon = staging / f"icon_{size}.png"
            icon.write_bytes(b"fake-png")
            icons[size] = icon
        return icons

    monkeypatch.setattr(desktop_entry, "_extract_icons", fake_extract)
    return app_dir, data_home, cfg_home


class TestRenderAutostart:
    def test_autostart_entry_content(self, xdg_env):
        content = desktop_entry.render_desktop(Path("/opt/apps/Zen_VocoType_Launcher.AppImage"), autostart=True)
        assert "Exec=/opt/apps/Zen_VocoType_Launcher.AppImage" in content
        assert "X-GNOME-Autostart-enabled=true" in content
        assert "@EXEC@" not in content  # 占位符必须全部渲染

    def test_menu_entry_has_no_autostart_flag(self, xdg_env):
        """菜单条目（autostart=False）不含自启动键——两条目不混淆。"""
        content = desktop_entry.render_desktop(Path("/opt/apps/Zen_VocoType_Launcher.AppImage"))
        assert "X-GNOME-Autostart-enabled" not in content


class TestInstallAutostart:
    def test_install_with_autostart_writes_entry(self, xdg_env):
        app_dir, data_home, cfg_home = xdg_env
        written = desktop_entry.install(app_dir, staging_root=app_dir / ".tmp", autostart=True)
        autostart = cfg_home / "autostart" / "zen-vocotype.desktop"
        assert autostart.is_file()
        assert autostart in written
        content = autostart.read_text(encoding="utf-8")
        assert "X-GNOME-Autostart-enabled=true" in content
        assert f"Exec={app_dir}/Zen_VocoType_Launcher.AppImage" in content
        # 图标仍走 XDG_DATA_HOME（不受 XDG_CONFIG_HOME 影响）
        assert (data_home / "icons" / "hicolor" / "64x64" / "apps" / "zen-vocotype.png").is_file()

    def test_install_idempotent(self, xdg_env):
        app_dir, _, _ = xdg_env
        first = desktop_entry.install(app_dir, staging_root=app_dir / ".tmp", autostart=True)
        second = desktop_entry.install(app_dir, staging_root=app_dir / ".tmp", autostart=True)
        assert {str(p) for p in first} == {str(p) for p in second}

    def test_install_without_autostart_default_unchanged(self, xdg_env):
        """默认 autostart=False：不写自启动条目（既有行为防回归）。"""
        app_dir, _, cfg_home = xdg_env
        desktop_entry.install(app_dir, staging_root=app_dir / ".tmp")
        assert not (cfg_home / "autostart" / "zen-vocotype.desktop").exists()


class TestUninstallSymmetry:
    def test_uninstall_removes_both_entries_and_icons(self, xdg_env):
        app_dir, data_home, cfg_home = xdg_env
        desktop_entry.install(app_dir, staging_root=app_dir / ".tmp", autostart=True)
        removed = desktop_entry.uninstall()
        assert not (cfg_home / "autostart" / "zen-vocotype.desktop").exists()
        assert not (data_home / "applications" / "zen-vocotype.desktop").exists()
        assert not (data_home / "icons" / "hicolor" / "64x64" / "apps" / "zen-vocotype.png").exists()
        assert len(removed) == 2 + len(desktop_entry.ICON_SIZES)

    def test_uninstall_without_autostart_installed_no_error(self, xdg_env):
        """未装 autostart 条目时卸载不报错（幂等，与安装期是否启用解耦）。"""
        app_dir, _, _ = xdg_env
        desktop_entry.install(app_dir, staging_root=app_dir / ".tmp")
        removed = desktop_entry.uninstall()
        assert len(removed) == 1 + len(desktop_entry.ICON_SIZES)

    def test_uninstall_nothing_installed(self, xdg_env):
        assert desktop_entry.uninstall() == []
