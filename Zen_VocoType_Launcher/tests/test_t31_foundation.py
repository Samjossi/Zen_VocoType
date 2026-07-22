"""T3.1 基座测试：配置校验、图标 helper、日志基座、资产迁移核对。"""

from pathlib import Path

import pytest
from pydantic import ValidationError

from zen_vocotype_launcher import icon_loader
from zen_vocotype_launcher.config import Settings
from zen_vocotype_launcher.logging_setup import setup_logging


class TestSettingsValidation:
    """数值型配置项启动校验（Field(gt=0)，非法即构造期拒绝）。"""

    def test_defaults(self):
        s = Settings()
        assert s.socket_wait_timeout_s == 15.0
        assert s.model_ready_timeout_s == 180.0
        assert s.ready_poll_interval_ms == 200
        assert s.terminate_grace_seconds == 5.0
        assert s.service_binary is None
        assert s.client_binary is None

    @pytest.mark.parametrize(
        "field",
        [
            "socket_wait_timeout_s",
            "model_ready_timeout_s",
            "ready_poll_interval_ms",
            "terminate_grace_seconds",
        ],
    )
    @pytest.mark.parametrize("bad", [0, -1, -0.5])
    def test_non_positive_rejected(self, field, bad):
        with pytest.raises(ValidationError):
            Settings(**{field: bad})

    def test_binary_override_accepted(self):
        s = Settings(service_binary="/opt/bin/svc", client_binary="/opt/bin/cli")
        assert s.service_binary == "/opt/bin/svc"
        assert s.client_binary == "/opt/bin/cli"


class TestIconLoader:
    """图标 helper 双环境路径解析（🔴 禁止 cwd 相对）。"""

    def test_assets_dir_source_layout(self):
        base = icon_loader.assets_dir()
        assert base.is_absolute()
        assert base.name == "assets"
        assert base.is_dir()  # T3.1 迁移后必须真实存在

    def test_icon_path_existing(self):
        path = icon_loader.icon_path(64)
        assert path is not None
        assert path.is_file()
        assert path.name == "zen_vocotype_launcher_icon_64.png"

    def test_icon_path_all_sizes(self):
        for size in (32, 64, 128, 256):
            assert icon_loader.icon_path(size) is not None, f"{size} 档缺失"

    def test_icon_path_missing_returns_none(self, tmp_path, monkeypatch):
        monkeypatch.setattr(icon_loader, "assets_dir", lambda: tmp_path)
        assert icon_loader.icon_path(64) is None  # 记 warning 不崩溃

    def test_icon_path_unknown_size_falls_back(self):
        path = icon_loader.icon_path(999)
        assert path is not None
        assert path.name == icon_loader.ICON_FILENAMES[icon_loader.DEFAULT_ICON_SIZE]


class TestMigratedAssets:
    """资产迁移清单 §2.3 销账核对：四尺寸就位、旧名零残留。"""

    def test_four_sizes_present(self):
        base = icon_loader.assets_dir()
        for size, name in icon_loader.ICON_FILENAMES.items():
            assert (base / name).is_file(), f"迁移缺失：{name}"

    def test_no_legacy_names(self):
        base = icon_loader.assets_dir()
        legacy = [p.name for p in base.iterdir() if p.name.startswith(("star_", "grid_chat"))]
        assert legacy == [], f"旧名残留：{legacy}"


class TestLoggingSetup:
    def test_setup_creates_log_file(self, tmp_path):
        log_file = setup_logging(tmp_path)
        assert log_file == tmp_path / "launcher.log"
        from loguru import logger

        logger.info("T3.1 测试写入")
        assert log_file.is_file()
