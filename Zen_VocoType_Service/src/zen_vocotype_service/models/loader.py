"""模型加载器（选型三/五）：注册表驱动的 AutoModel 构造与试推理自检。

- ``model_id`` 条目经 MODELSCOPE_CACHE 缓存命中/在线下载；
  ``local_path`` 条目直接本地加载
- 显式 ``disable_update=True`` 防在线检查拖慢启动
- 加载失败抛带真实原因的 ``ModelLoadError``，🔴 无任何假模型兜底
- 自检以内置真实语音 PCM 跑一次推理（🔴 禁止空输入假装自检），
  仅验证「推理通路可用 + 返回结构合法」，不验证识别质量

⚠️ import 顺序敏感：``MODELSCOPE_CACHE`` 必须在 import funasr/modelscope
之前设置（入口 main.py 第一行，单测固化）；本模块函数内延迟导入作为
第二道防线，但不替代入口保证。
"""

import sys
from pathlib import Path

from zen_vocotype_protocol.paths import DEFAULT_SAMPLE_RATE

from zen_vocotype_service.config import COMPONENT_ROOT, ModelEntry
from zen_vocotype_service.logging_setup import logger

#: 自检音频资产（真实语音片段，来源：paraformer-large 模型仓库示例
#: asr_example.wav 转 16kHz/16bit/单声道 PCM 并截取前 3 秒）。
#: 双环境解析（与 tray/icon_loader 同一 ``_MEIPASS`` 约定，阶段 4 T4.2 修：
#: 原 ``COMPONENT_ROOT / "assets"`` 在打包形态指向产物根而非 ``_internal``）；
#: 延迟为函数调用而非模块常量——``_MEIPASS`` 须在运行期读取
def _selftest_pcm_path() -> Path:
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass is not None:  # PyInstaller 打包形态
        return Path(meipass) / "assets" / "selftest_16k.pcm"
    return COMPONENT_ROOT / "assets" / "selftest_16k.pcm"

#: FunASR 推理归一化系数（16bit PCM → float32）
PCM_NORMALIZE: float = 32768.0


class ModelLoadError(Exception):
    """模型加载/自检失败，message 含真实原因。"""


class LoadedModel:
    """已加载模型句柄：AutoModel 实例 + 注册名 + 来源。"""

    def __init__(self, name: str, entry: ModelEntry, model) -> None:
        self.name: str = name
        self.entry: ModelEntry = entry
        self.model = model

    def release(self) -> None:
        """释放模型资源（解除引用，交由 GC/显存回收）。"""
        self.model = None


def _build_automl_params(entry: ModelEntry) -> dict:
    """按注册表条目集中构造 AutoModel 参数（R3：参数漂移集中此处应对）。"""
    if entry.local_path is not None:
        params: dict = {"model": str(entry.local_path)}
    else:
        params = {"model": entry.model_id}
    if entry.vad_model_id:
        params["vad_model"] = entry.vad_model_id
    if entry.punc_model_id:
        params["punc_model"] = entry.punc_model_id
    params["disable_update"] = True
    params["device"] = "cpu"
    #: 引擎特定附加参数最后并入（例：Fun-ASR-Nano 的 trust_remote_code/remote_code）
    params.update(entry.extra_params)
    return params


def load_model(name: str, entry: ModelEntry) -> LoadedModel:
    """按注册表条目加载模型（引擎类型分支唯一加载点）。

    :raises ModelLoadError: 加载失败，message 含真实原因
    """
    if entry.engine_type == "qwen3-asr":
        return _load_qwen3_asr(name, entry)
    params = _build_automl_params(entry)
    logger.info("开始加载模型 {}（{}）", name, entry.source)
    try:
        from funasr import AutoModel  # 延迟导入：MODELSCOPE_CACHE 须先就位

        model = AutoModel(**params)
    except Exception as exc:
        raise ModelLoadError(f"模型 {name!r} 加载失败: {exc}") from exc
    logger.info("模型 {} 加载完成", name)
    return LoadedModel(name, entry, model)


def _load_qwen3_asr(name: str, entry: ModelEntry) -> LoadedModel:
    """加载 Qwen3-ASR 引擎（transformers 后端，CPU）。

    ``model_id`` 条目先经 modelscope 下载到 MODELSCOPE_CACHE（与 FunASR
    同一缓存目录统一管理），再取本地路径交给 ``Qwen3ASRModel``；
    ``local_path`` 条目直接本地加载。
    """
    logger.info("开始加载模型 {}（{}，引擎 qwen3-asr）", name, entry.source)
    try:
        from modelscope import snapshot_download  # 延迟导入，同 funasr 策略
        from qwen_asr import Qwen3ASRModel

        if entry.local_path is not None:
            model_path = str(entry.local_path)
        else:
            model_path = snapshot_download(entry.model_id)
        model = Qwen3ASRModel.from_pretrained(
            model_path,
            **{"device_map": "cpu", **entry.extra_params},
        )
    except Exception as exc:
        raise ModelLoadError(f"模型 {name!r} 加载失败: {exc}") from exc
    logger.info("模型 {} 加载完成", name)
    return LoadedModel(name, entry, model)


def pcm_to_float_array(pcm: bytes):
    """16kHz/16bit/单声道 PCM 字节流转 float32 归一化数组（无第三方音频库）。"""
    import numpy as np

    return np.frombuffer(pcm, dtype=np.int16).astype(np.float32) / PCM_NORMALIZE


def run_inference(loaded: LoadedModel, audio) -> dict:
    """对已加载模型跑一次推理（引擎类型分支唯一推理点）。

    :param audio: float32 归一化数组（``pcm_to_float_array`` 产物）
    :return: ``{"text", "confidence"}``；confidence 模型不给时为 None（🔴 禁止编造）
    :raises RuntimeError: 推理失败或返回结构非法
    """
    if loaded.entry.engine_type == "qwen3-asr":
        results = loaded.model.transcribe(audio=(audio, DEFAULT_SAMPLE_RATE))
        if not results:
            raise RuntimeError("推理返回结构非法: 空结果列表")
        return {"text": results[0].text, "confidence": None}
    result = loaded.model.generate(input=audio, cache={}, batch_size_s=60)
    if not isinstance(result, list) or not result or "text" not in result[0]:
        raise RuntimeError(f"推理返回结构非法: {type(result).__name__}")
    item = result[0]
    return {"text": item["text"], "confidence": item.get("confidence")}


def selftest(loaded: LoadedModel) -> None:
    """试推理自检：真实语音 PCM 跑一次推理，验证通路与返回结构。

    :raises ModelLoadError: 自检失败（🔴 加载成功不等于可用）
    """
    pcm_path = _selftest_pcm_path()
    if not pcm_path.exists():
        raise ModelLoadError(f"自检音频缺失: {pcm_path}")
    audio = pcm_to_float_array(pcm_path.read_bytes())
    try:
        outcome = run_inference(loaded, audio)
    except Exception as exc:
        raise ModelLoadError(f"模型 {loaded.name!r} 试推理自检失败: {exc}") from exc
    if not isinstance(outcome["text"], str):
        raise ModelLoadError(
            f"模型 {loaded.name!r} 自检返回结构非法: text 非字符串"
        )
    logger.info("模型 {} 自检通过（返回文本长度 {}）", loaded.name, len(outcome["text"]))
