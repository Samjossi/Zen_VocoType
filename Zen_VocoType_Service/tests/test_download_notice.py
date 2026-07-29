"""下载打点单元测试（模型缺失与下载提醒计划 T2/T3）。

- ``ServiceState`` 下载标记的置位/清除/默认值；
- ``ModelManager``：未缓存路径进出打点对称（含加载异常 finally 清除，
  🔴 防状态泄漏卡死在「下载中」）；已缓存路径不打点；state 缺省仅日志。
"""

import pytest

from zen_vocotype_service.config import ModelEntry, Settings
from zen_vocotype_service.models import manager as manager_mod
from zen_vocotype_service.models.loader import ModelLoadError
from zen_vocotype_service.models.manager import ModelManager, ModelSwitchError
from zen_vocotype_service.state import ServiceState


class TestDownloadingState:
    def test_default_none(self):
        assert ServiceState().downloading_model is None

    def test_mark_and_clear(self):
        state = ServiceState()
        state.mark_downloading("sensevoice-small")
        assert state.downloading_model == "sensevoice-small"
        state.clear_downloading()
        assert state.downloading_model is None

    def test_clear_idempotent(self):
        """无下载进行中时清除不报错（防御性）。"""
        state = ServiceState()
        state.clear_downloading()
        assert state.downloading_model is None

    def test_mark_does_not_touch_status(self):
        """下载标记是辅助标记，不改变 status 语义（计划 D2）。"""
        state = ServiceState()
        state.mark_ready("fun-asr-nano")
        state.mark_downloading("qwen3-asr-1.7b")
        assert state.status == "ready"
        assert state.current_model == "fun-asr-nano"


class _FakeLoaded:
    """最小 LoadedModel 替身（manager 打点测试不触碰真实引擎）。"""

    def __init__(self, name: str) -> None:
        self.name = name
        self.released = False

    def release(self) -> None:
        self.released = True


@pytest.fixture()
def settings(tmp_path) -> Settings:
    """双条目注册表：cached（缓存目录就位）/ uncached（未缓存）。"""
    (tmp_path / "models" / "iic--Cached").mkdir(parents=True)
    return Settings(
        models={
            "cached": ModelEntry(model_id="iic/Cached"),
            "uncached": ModelEntry(model_id="iic/Uncached"),
        },
        default_model="cached",
        models_dir=tmp_path,
    )


@pytest.fixture()
def patch_loader(monkeypatch):
    """替换真实 load_model/selftest；记录 load_model 调用时的下载标记快照。"""
    seen_downloading: list[str | None] = []
    state_ref: dict[str, ServiceState | None] = {"state": None}

    def _fake_load(name, entry):
        state = state_ref["state"]
        seen_downloading.append(state.downloading_model if state else None)
        return _FakeLoaded(name)

    monkeypatch.setattr(manager_mod, "load_model", _fake_load)
    monkeypatch.setattr(manager_mod, "selftest", lambda loaded: None)
    return seen_downloading, state_ref


class TestManagerDownloadNotice:
    def test_uncached_marks_during_load_and_clears_after(
        self, settings, patch_loader
    ):
        """未缓存：load_model 执行期间标记可见，返回后已清除（对称打点）。"""
        seen, state_ref = patch_loader
        state = ServiceState()
        state_ref["state"] = state
        manager = ModelManager(settings, state)
        manager.load_initial("uncached")
        assert seen == ["uncached"]
        assert state.downloading_model is None

    def test_cached_never_marks(self, settings, patch_loader):
        """已缓存：全程不打点（行为与现状一致）。"""
        seen, state_ref = patch_loader
        state = ServiceState()
        state_ref["state"] = state
        manager = ModelManager(settings, state)
        manager.load_initial("cached")
        assert seen == [None]
        assert state.downloading_model is None

    def test_load_failure_still_clears(self, settings, monkeypatch):
        """🔴 加载抛异常也必须 finally 清除标记（防状态泄漏卡死「下载中」）。"""
        def _fail(name, entry):
            raise ModelLoadError("网络不可达")

        monkeypatch.setattr(manager_mod, "load_model", _fail)
        state = ServiceState()
        manager = ModelManager(settings, state)
        with pytest.raises(ModelLoadError):
            manager.load_initial("uncached")
        assert state.downloading_model is None

    def test_switch_uncached_marks_and_failure_clears(
        self, settings, monkeypatch, patch_loader
    ):
        """切换路径：先载 cached，再切 uncached 时打点；切换加载失败同样清除。"""
        seen, state_ref = patch_loader
        state = ServiceState()
        state_ref["state"] = state
        manager = ModelManager(settings, state)
        manager.load_initial("cached")

        def _fail(name, entry):
            raise ModelLoadError("磁盘满")

        monkeypatch.setattr(manager_mod, "load_model", _fail)
        with pytest.raises(ModelSwitchError, match="LOAD_FAILED"):
            manager.switch("uncached")
        assert state.downloading_model is None
        assert manager.current.name == "cached"  # 旧模型不受影响（回滚语义不变）

    def test_switch_success_with_download_notice(self, settings, patch_loader):
        """切换成功路径：uncached 目标在加载期间打点，完成后清除并切换。"""
        seen, state_ref = patch_loader
        state = ServiceState()
        state_ref["state"] = state
        manager = ModelManager(settings, state)
        manager.load_initial("cached")
        manager.switch("uncached")
        assert seen == [None, "uncached"]
        assert state.downloading_model is None
        assert manager.current.name == "uncached"

    def test_state_none_only_logs(self, settings, patch_loader):
        """state 缺省（独立使用/旧测试形态）：仅日志，不打状态、不报错。"""
        manager = ModelManager(settings)
        manager.load_initial("uncached")
        assert manager.current.name == "uncached"

    def test_uncached_logs_download_message(self, settings, monkeypatch):
        """未缓存打点必须记 info 日志（headless 降级路径的信息保障，D4）。"""
        from zen_vocotype_service.logging_setup import logger

        monkeypatch.setattr(
            manager_mod, "load_model", lambda name, entry: _FakeLoaded(name)
        )
        monkeypatch.setattr(manager_mod, "selftest", lambda loaded: None)
        messages: list[str] = []
        handler_id = logger.add(lambda m: messages.append(str(m)), level="INFO")
        try:
            ModelManager(settings).load_initial("uncached")
        finally:
            logger.remove(handler_id)
        assert any("未缓存" in m and "下载" in m for m in messages)
