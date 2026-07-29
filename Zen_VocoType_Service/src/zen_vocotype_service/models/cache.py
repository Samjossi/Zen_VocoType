"""模型缓存探测（单一出处，模型缺失与下载提醒计划 T1/D1）。

「条目所需权重是否已全部缓存到本地」的判定**唯一出处**为本模块
``is_model_cached``；托盘模型清单的展示文案（``tray/models_dialog``）与
``ModelManager`` 的下载打点均调用本函数，禁止第二份探测逻辑。

缓存布局：modelscope 默认 ``<MODELSCOPE_CACHE>/models/<org>--<name>``
（``MODELSCOPE_CACHE`` 在入口被硬设置为 ``settings.models_dir``）。

🔴 本模块为核心层纯逻辑，零 Qt 依赖。
"""

from pathlib import Path

from zen_vocotype_service.config import ModelEntry
from zen_vocotype_service.logging_setup import logger
from zen_vocotype_service.models.loader import GGUF_DEFAULT_VAD_REPO

#: modelscope 缓存目录布局（<models_dir>/models/<org>--<name>）
_CACHE_SUBDIR = "models"


def _cache_dir(model_id: str, models_dir: Path) -> Path:
    """model_id 对应的 modelscope 缓存目录。"""
    return Path(models_dir) / _CACHE_SUBDIR / model_id.replace("/", "--")


def _is_dir_present(path: Path) -> bool:
    """目录存在性探测；🔴 探测失败（OSError）按未缓存处理并记 warning，禁止静默。"""
    try:
        return path.is_dir()
    except OSError as exc:
        logger.warning("缓存探测失败（按未缓存处理）：{}（{}）", path, exc)
        return False


def is_model_cached(entry: ModelEntry, models_dir: Path) -> bool:
    """判定条目运行时所需权重是否已全部缓存到本地。

    分支规则：

    - ``local_path`` 条目：本地直载，恒 True（不触发下载）
    - ``model_id`` 条目：主仓库缓存目录须存在
    - ``funasr-gguf`` 条目：权重仓 + VAD 共享仓（``extra_params.vad_repo``
      或默认 ``GGUF_DEFAULT_VAD_REPO``）**两者都命中**才算已缓存
      （🔴 只查权重仓会漏报 VAD 下载）
    - ``vad_model_id`` / ``punc_model_id`` 附属模型同样会触发下载，
      任一缺失视为未缓存
    """
    if entry.model_id is None:
        return True
    if not _is_dir_present(_cache_dir(entry.model_id, models_dir)):
        return False
    if entry.engine_type == "funasr-gguf":
        vad_repo = entry.extra_params.get("vad_repo", GGUF_DEFAULT_VAD_REPO)
        if not _is_dir_present(_cache_dir(vad_repo, models_dir)):
            return False
    for aux_id in (entry.vad_model_id, entry.punc_model_id):
        if aux_id is not None and not _is_dir_present(
            _cache_dir(aux_id, models_dir)
        ):
            return False
    return True


__all__ = ["is_model_cached"]
