"""T2.1 单元测试：配置校验、热键表达式解析、图标 helper 双环境解析、日志基座。"""

import sys
from pathlib import Path

import pytest
from pydantic import ValidationError

from zen_vocotype_client.config import Settings, validate_startup
from zen_vocotype_client.hotkey.combo import modifier_name, parse_hotkey
from zen_vocotype_client.tray import icon_loader


# ---------------------------------------------------------------------------
# 热键表达式解析（combo 纯逻辑）
# ---------------------------------------------------------------------------

class TestParseHotkey:
    def test_default_expression(self):
        combo = parse_hotkey("<ctrl>+`")
        assert combo.modifiers == frozenset({"ctrl"})
        assert combo.expression == "<ctrl>+`"

    def test_multi_modifiers(self):
        combo = parse_hotkey("<ctrl>+<shift>+a")
        assert combo.modifiers == frozenset({"ctrl", "shift"})

    def test_no_modifier_allowed(self):
        combo = parse_hotkey("<f9>")
        assert combo.modifiers == frozenset()

    def test_empty_rejected(self):
        with pytest.raises(ValueError, match="为空"):
            parse_hotkey("")

    def test_garbage_rejected(self):
        with pytest.raises(ValueError):
            parse_hotkey("<ctrl>+<nonexistent_key_xyz>")

    def test_modifier_only_rejected(self):
        with pytest.raises(ValueError, match="主键"):
            parse_hotkey("<ctrl>+<shift>")

    def test_multi_normal_keys_rejected(self):
        with pytest.raises(ValueError, match="主键"):
            parse_hotkey("a+b")

    def test_modifier_name_mapping(self):
        from pynput import keyboard

        assert modifier_name(keyboard.Key.ctrl_l) == "ctrl"
        assert modifier_name(keyboard.Key.shift_r) == "shift"
        assert modifier_name(keyboard.Key.space) is None


# ---------------------------------------------------------------------------
# 配置启动校验
# ---------------------------------------------------------------------------

class TestSettingsValidation:
    def test_defaults(self):
        s = Settings(socket_path="/tmp/x.sock")
        assert s.hotkey == "<ctrl>+<alt>+o"  # 与旧 GridChat 及本机已占用组合明确区分
        assert s.paste_restore_delay_ms == 500
        assert s.max_record_seconds == 60
        assert s.input_device is None
        assert s.notify_dedup_seconds == 5.0
        assert s.enable_sound_notify is False

    def test_negative_delay_rejected(self):
        with pytest.raises(ValidationError):
            Settings(socket_path="/tmp/x.sock", paste_restore_delay_ms=-1)

    def test_zero_max_record_rejected(self):
        with pytest.raises(ValidationError):
            Settings(socket_path="/tmp/x.sock", max_record_seconds=0)

    def test_validate_startup_ok(self):
        validate_startup(Settings(socket_path="/tmp/x.sock"))

    def test_validate_startup_bad_hotkey(self):
        s = Settings(socket_path="/tmp/x.sock", hotkey="<ctrl>+<bad_key_xyz>")
        with pytest.raises(ValueError):
            validate_startup(s)


# ---------------------------------------------------------------------------
# 图标 helper 双环境解析
# ---------------------------------------------------------------------------

class TestIconLoader:
    def test_assets_dir_source_layout(self):
        base = icon_loader.assets_dir()
        assert base.is_dir()
        assert base.name == "assets"
        # 源码布局下组件根应为 Zen_VocoType_Client
        assert base.parent.name == "Zen_VocoType_Client"

    def test_assets_dir_meipass(self, monkeypatch, tmp_path):
        monkeypatch.setattr(sys, "_MEIPASS", str(tmp_path), raising=False)
        assert icon_loader.assets_dir() == tmp_path / "assets"

    def test_icons_migrated(self):
        base = icon_loader.assets_dir()
        for name in icon_loader.ICON_FILENAMES:
            assert (base / name).is_file(), f"图标未迁移: {name}"
        assert "grid_chat" not in " ".join(icon_loader.ICON_FILENAMES)


# ---------------------------------------------------------------------------
# 日志基座
# ---------------------------------------------------------------------------

class TestLoggingSetup:
    def test_setup_creates_file(self, tmp_path):
        from loguru import logger

        from zen_vocotype_client.logging_setup import setup_logging

        setup_logging(tmp_path)
        logger.info("测试日志落盘")
        logger.complete()
        log_file = tmp_path / "client.log"
        assert log_file.is_file()
        assert "测试日志落盘" in log_file.read_text(encoding="utf-8")
