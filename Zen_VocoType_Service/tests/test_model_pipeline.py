"""T1.4/T1.5 集成测试：注册表驱动真实加载、自检、原子切换。

🔴 真实模型加载测试（慢速，标记 slow）：两个注册表默认模型各加载一次并
自检；切换后 model_info 交叉验证；加载失败回滚旧模型不受影响。
缓存命中结论记录于阶段 1 验收记录（T1.6）。
"""

import os
from pathlib import Path

import pytest

from zen_vocotype_service.config import COMPONENT_ROOT, Settings

# ⚠️ 必须在 funasr/modelscope 首次导入前设置（模块级，先于 loader 延迟导入）
os.environ["MODELSCOPE_CACHE"] = str(COMPONENT_ROOT / "models")

from zen_vocotype_service.models.loader import ModelLoadError, load_model, selftest
from zen_vocotype_service.models.manager import ModelManager, ModelSwitchError
from zen_vocotype_service.models.registry import ModelNotRegisteredError, get_entry

pytestmark = pytest.mark.slow


@pytest.fixture(scope="module")
def settings() -> Settings:
    return Settings()


class TestRegistryLoad:
    def test_paraformer_load_and_selftest(self, settings):
        entry = get_entry(settings, "paraformer-large")
        loaded = load_model("paraformer-large", entry)
        try:
            selftest(loaded)  # 不抛异常即通过
        finally:
            loaded.release()

    def test_sensevoice_load_and_selftest(self, settings):
        entry = get_entry(settings, "sensevoice-small")
        loaded = load_model("sensevoice-small", entry)
        try:
            selftest(loaded)
        finally:
            loaded.release()

    def test_load_failure_has_real_reason(self, settings):
        bad_entry = settings.models["paraformer-large"].model_copy(
            update={"local_path": Path("/nonexistent/model"), "model_id": None}
        )
        with pytest.raises(ModelLoadError, match="加载失败"):
            load_model("bad", bad_entry)


class TestAtomicSwitch:
    def test_switch_cross_verify_and_rollback(self, settings):
        manager = ModelManager(settings)
        manager.load_initial("paraformer-large")
        try:
            assert manager.model_info()["current_model"] == "paraformer-large"

            # 目标不在注册表 → ModelNotRegisteredError，当前模型不变
            with pytest.raises(ModelNotRegisteredError):
                manager.switch("no-such-model")
            assert manager.current.name == "paraformer-large"

            # 真实切换到 sensevoice-small，model_info 交叉验证
            manager.switch("sensevoice-small")
            info = manager.model_info()
            assert info["current_model"] == "sensevoice-small"
            loaded_flags = {m["name"]: m["loaded"] for m in info["available_models"]}
            assert loaded_flags == {"paraformer-large": False, "sensevoice-small": True}

            # 加载失败回滚：注入坏条目，旧模型不受影响
            settings.models["broken"] = settings.models[
                "paraformer-large"
            ].model_copy(
                update={"local_path": Path("/nonexistent/model"), "model_id": None}
            )
            try:
                with pytest.raises(ModelSwitchError, match="LOAD_FAILED"):
                    manager.switch("broken")
                assert manager.current.name == "sensevoice-small"
            finally:
                del settings.models["broken"]

            # 切回 paraformer-large
            manager.switch("paraformer-large")
            assert manager.model_info()["current_model"] == "paraformer-large"
        finally:
            manager.release()
