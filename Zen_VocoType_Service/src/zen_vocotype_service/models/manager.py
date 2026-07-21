"""模型管理器：原子切换（先备后切，选型三）。

时序：加载新模型（失败 → 旧模型不受影响）→ 试推理自检（失败 → 释放新模型
回滚）→ 替换引用 → 释放旧模型 → ``model_info`` 可交叉验证。

🔴 本类的公开方法只许在推理 worker 单线程内调用（选型四：切换与推理天然
互斥），类内不再另设锁。
"""

from zen_vocotype_service.config import Settings
from zen_vocotype_service.logging_setup import logger
from zen_vocotype_service.models.loader import (
    LoadedModel,
    ModelLoadError,
    load_model,
    selftest,
)
from zen_vocotype_service.models.registry import ModelNotRegisteredError, get_entry, list_models


class ModelSwitchError(Exception):
    """模型切换失败（加载失败/自检失败），message 含真实原因。"""


class ModelManager:
    """当前模型引用与注册表驱动的加载/切换。"""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._current: LoadedModel | None = None

    @property
    def current(self) -> LoadedModel | None:
        return self._current

    def load_initial(self, model_name: str) -> None:
        """启动加载默认模型 + 自检；失败抛 ``ModelLoadError`` 带真实原因。"""
        entry = get_entry(self._settings, model_name)
        loaded = load_model(model_name, entry)
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
            loaded = load_model(model_name, entry)
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
