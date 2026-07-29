"""模型管理器：原子切换（先备后切，选型三）。

时序：加载新模型（失败 → 旧模型不受影响）→ 试推理自检（失败 → 释放新模型
回滚）→ 替换引用 → 释放旧模型 → ``model_info`` 可交叉验证。

下载打点（模型缺失与下载提醒计划 D2）：加载前缓存探测未命中时置
``ServiceState.downloading_model`` + 记日志，加载结束（成败）经 finally
清除——本类是启动首载 / 托盘切换 / Socket 切换三条路径的唯一汇流点，
此处打点即全覆盖，调用方无需各自处理。

🔴 本类的公开方法只许在推理 worker 单线程内调用（选型四：切换与推理天然
互斥），类内不再另设锁。
"""

from zen_vocotype_service.config import ModelEntry, Settings
from zen_vocotype_service.logging_setup import logger
from zen_vocotype_service.models.cache import is_model_cached
from zen_vocotype_service.models.loader import (
    LoadedModel,
    ModelLoadError,
    load_model,
    selftest,
)
from zen_vocotype_service.models.registry import ModelNotRegisteredError, get_entry, list_models
from zen_vocotype_service.state import ServiceState


class ModelSwitchError(Exception):
    """模型切换失败（加载失败/自检失败），message 含真实原因。"""


class ModelManager:
    """当前模型引用与注册表驱动的加载/切换。"""

    def __init__(self, settings: Settings, state: ServiceState | None = None) -> None:
        self._settings = settings
        #: 下载标记目标（可选）：None 时仅记日志不打状态（测试/独立使用场景）
        self._state = state
        self._current: LoadedModel | None = None

    @property
    def current(self) -> LoadedModel | None:
        return self._current

    def _load_with_download_notice(
        self, model_name: str, entry: ModelEntry
    ) -> LoadedModel:
        """加载模型；未缓存时置下载标记 + 日志，结束（成败）一律清除。

        🔴 清除在 finally：加载抛异常也不得把状态泄漏卡死在「下载中」。
        """
        downloading = not is_model_cached(entry, self._settings.models_dir)
        if downloading:
            logger.info("模型 {} 未缓存，开始从 ModelScope 下载…", model_name)
            if self._state is not None:
                self._state.mark_downloading(model_name)
        try:
            return load_model(model_name, entry)
        finally:
            if downloading and self._state is not None:
                self._state.clear_downloading()

    def load_initial(self, model_name: str) -> None:
        """启动加载默认模型 + 自检；失败抛 ``ModelLoadError`` 带真实原因。"""
        entry = get_entry(self._settings, model_name)
        loaded = self._load_with_download_notice(model_name, entry)
        try:
            selftest(loaded)
        except ModelLoadError:
            loaded.release()
            raise
        self._current = loaded

    def switch(self, model_name: str) -> None:
        """原子切换到目标模型（先备后切 + 自检，失败自动回滚）。

        :raises ModelNotRegisteredError: 目标不在注册表（3001）
        :raises ModelSwitchError: 加载失败（3002）或自检失败（3003），旧模型不受影响
        """
        if self._current is not None and model_name == self._current.name:
            logger.info("目标模型 {} 即当前模型，无需切换", model_name)
            return
        entry = get_entry(self._settings, model_name)
        try:
            loaded = self._load_with_download_notice(model_name, entry)
        except ModelLoadError as exc:
            raise ModelSwitchError(f"LOAD_FAILED: {exc}") from exc
        try:
            selftest(loaded)
        except ModelLoadError as exc:
            loaded.release()
            raise ModelSwitchError(f"SELFTEST_FAILED: {exc}") from exc
        old = self._current
        self._current = loaded
        if old is not None:
            old.release()
            logger.info("旧模型 {} 已释放", old.name)
        logger.info("模型切换完成: {} -> {}", old.name if old else None, model_name)

    def model_info(self) -> dict:
        """当前模型 + 注册表全量列表（含加载来源、loaded 标记）。"""
        current_name = self._current.name if self._current else None
        return {
            "current_model": current_name,
            "available_models": [
                {**item, "loaded": item["name"] == current_name}
                for item in list_models(self._settings)
            ],
        }

    def release(self) -> None:
        if self._current is not None:
            self._current.release()
            self._current = None
