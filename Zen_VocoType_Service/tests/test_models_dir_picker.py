"""T4.1b 单元测试：自选模型目录校验三分支（验收标准 7）。

校验逻辑为纯函数（无 Qt 依赖），GUI 路径在托盘测试中经 offscreen 覆盖。
"""

import os
from pathlib import Path

import pytest

from zen_vocotype_service.tray.models_dir_picker import validate_models_dir


class TestValidateModelsDir:
    def test_nonexistent_rejected(self, tmp_path):
        reason = validate_models_dir(tmp_path / "no_such_dir")
        assert reason is not None and "不存在" in reason

    def test_file_not_dir_rejected(self, tmp_path):
        f = tmp_path / "a_file"
        f.write_text("x", encoding="utf-8")
        reason = validate_models_dir(f)
        assert reason is not None and "不是目录" in reason

    def test_appimage_mount_point_rejected(self, tmp_path):
        """路径任一段为 .mount_* 即拒绝（/tmp 与 XDG_RUNTIME_DIR 两种挂载形态）。"""
        mount_like = tmp_path / ".mount_FakeAp12" / "sub"
        mount_like.mkdir(parents=True)
        reason = validate_models_dir(mount_like)
        assert reason is not None and "挂载点" in reason

    @pytest.mark.skipif(os.geteuid() == 0, reason="root 下权限位不生效")
    def test_unwritable_rejected(self, tmp_path):
        ro = tmp_path / "readonly"
        ro.mkdir()
        ro.chmod(0o555)
        try:
            reason = validate_models_dir(ro)
            assert reason is not None and "不可写" in reason
        finally:
            ro.chmod(0o755)  # 恢复以便 tmp_path 清理

    def test_existing_writable_dir_accepted(self, tmp_path):
        ok = tmp_path / "models"
        ok.mkdir()
        assert validate_models_dir(ok) is None
