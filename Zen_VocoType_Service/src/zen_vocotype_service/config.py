"""服务端配置（单一配置源，选型 7 定稿）。

每组件恰好一个 ``Settings`` 类 + 一个 ``config.yaml`` + 环境变量覆盖：

- 配置文件：组件根目录 ``config.yaml``（位置推算见契约库 ``settings.component_root``）
- 环境变量前缀：``ZEN_VOCOTYPE_SERVICE_``
- 配置源优先级与组件根推算的**行为逻辑单一出处**为契约库
  ``zen_vocotype_protocol.settings``，本文件仅声明字段与默认值
- Socket 路径默认值唯一出处为契约库 ``zen_vocotype_protocol.paths``，此处仅允许覆盖
- 模型注册表内嵌于本配置（选型二），缺省内置 fun-asr-nano（默认）、
  sensevoice-small 与 qwen3-asr-1.7b 三条
"""

from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator

from zen_vocotype_protocol.paths import (
    DEFAULT_LOG_DIR,
    DEFAULT_MODELS_DIR,
    DEFAULT_SOCKET_PATH,
)
from zen_vocotype_protocol.settings import ComponentSettings, component_model_config, component_root

#: 组件根目录（基于本文件自身位置解析；打包形态限制见 component_root 文档）
COMPONENT_ROOT: Path = component_root(__file__)

#: 默认配置文件路径
CONFIG_FILE: Path = COMPONENT_ROOT / "config.yaml"

#: 推理超时预算默认值（秒）。依据：协议 ``MAX_BODY_BYTES`` 约合 10 分钟录音，
#: 默认引擎 fun-asr-nano 分钟级长音频实测 RTF≈0.27（2026-07-23，165.7s 音频
#: 推理 45.2s），10 分钟录音推理 ≈164s，本值 = 164s × 安全系数 1.8 取整；
#: 附带收益：模型切换共用本预算，首次切换未缓存大模型的下载时间亦被覆盖
DEFAULT_INFER_TIMEOUT_S: float = 300.0

#: 推理队列积压阈值（选型四）：超过即拒绝新请求返回 2002，防不可预期延迟
DEFAULT_QUEUE_MAX_PENDING: int = 4

#: 连接数上限（选型一）：防御性限制，正常仅 1 客户端长连接 + Launcher 探测
DEFAULT_MAX_CONNECTIONS: int = 8


class ModelEntry(BaseModel):
    """模型注册表条目：``model_id``（缓存/在线）与 ``local_path``（本地直载）二选一。"""

    model_id: str | None = None
    local_path: Path | None = None
    vad_model_id: str | None = None
    punc_model_id: str | None = None
    #: 展示层描述（托盘「模型清单…」用；🔴 属展示元数据，不进协议 model_info 响应）
    description: str = ""
    #: 引擎类型：加载/推理分支的唯一依据（loader/worker 各一处 if）。
    #: 默认 "funasr"，现有条目与用户旧配置零感知
    engine_type: Literal["funasr", "qwen3-asr", "funasr-gguf"] = "funasr"
    #: 引擎特定加载附加参数：
    #: funasr → 原样并入 AutoModel()（例：Fun-ASR-Nano 的 trust_remote_code/remote_code）
    #: qwen3-asr → 原样并入 Qwen3ASRModel.from_pretrained()
    #: funasr-gguf → GGUF 文件名/仓库覆盖（encoder/llm/vad/vad_repo，均有默认约定）
    extra_params: dict[str, Any] = {}

    @model_validator(mode="after")
    def _check_source_exclusive(self) -> "ModelEntry":
        if (self.model_id is None) == (self.local_path is None):
            raise ValueError("模型条目必须且只能提供 model_id 或 local_path 之一")
        return self

    @property
    def source(self) -> str:
        """加载来源描述（model_info 响应用）。"""
        if self.model_id is not None:
            return f"model_id:{self.model_id}"
        return f"local_path:{self.local_path}"


#: 内置默认注册表（用户可在 config.yaml 的 models 段覆盖/扩充）
#: 2026-07-23 移除 paraformer-large/seaco-paraformer（评估记录：work plans/
#: 2026-0723-0623_旧引擎移除评估记录.md），默认引擎同步迁至 fun-asr-nano
#: 2026-07-23 fun-asr-nano 切换为 GGUF/llama.cpp 运行时（提速修复计划：
#: work plans/2026-0723-0726_fun-asr-nano提速修复（GGUF集成）实现计划.md）。
#: PyTorch 版恢复方法（备选）：engine_type 改回 "funasr"，条目替换为
#: model_id=FunAudioLLM/Fun-ASR-Nano-2512 +
#: vad_model_id=iic/speech_fsmn_vad_zh-cn-16k-common-pytorch +
#: extra_params={trust_remote_code: true, remote_code: ./model.py}
DEFAULT_MODEL_REGISTRY: dict[str, dict] = {
    "fun-asr-nano": {
        "model_id": "FunAudioLLM/Fun-ASR-Nano-GGUF",
        "engine_type": "funasr-gguf",
        "extra_params": {
            "encoder": "funasr-encoder-f16.gguf",
            "llm": "qwen3-0.6b-q8_0.gguf",
            "vad_repo": "FunAudioLLM/fsmn-vad-GGUF",
            "vad": "fsmn-vad.gguf",
        },
        "description": "通用离线识别（默认）。新一代 LLM-ASR（阿里通义 2025-12，0.8B），"
        "自带标点，支持中/英/日及中文七大方言、歌词说唱识别与热词注入，"
        "难例与长尾词表现优于上一代模型。GGUF/llama.cpp 运行时（q8_0），"
        "CPU 实测 RTF≈0.08–0.12，说完即贴。",
    },
    "sensevoice-small": {
        "model_id": "iic/SenseVoiceSmall",
        "vad_model_id": "iic/speech_fsmn_vad_zh-cn-16k-common-pytorch",
        "description": "多语言语音理解：中/粤/英/日/韩等 50+ 语种，"
        "兼具情感识别与声音事件检测；推理极快（10 秒音频约 70ms）。",
    },
    "qwen3-asr-1.7b": {
        "model_id": "Qwen/Qwen3-ASR-1.7B",
        "engine_type": "qwen3-asr",
        "description": "高精度多语言识别（阿里 Qwen 2026-01，1.7B）。开源 ASR SOTA："
        "30 种语言 + 22 种中文方言，支持热词/上下文增强与最长 20 分钟单条音频，"
        "复杂声学环境与歌声识别强。⚠️ CPU 实测 RTF≈1.2（慢于实时），"
        "仅建议短音频或实验性使用。",
    },
}


class Settings(ComponentSettings):
    """服务端全部配置项的唯一入口。"""

    model_config = component_model_config(__file__, "ZEN_VOCOTYPE_SERVICE_")

    socket_path: str = DEFAULT_SOCKET_PATH
    #: 模型根目录（MODELSCOPE_CACHE 指向）。默认 XDG 数据目录（契约库唯一出处），
    #: 🔴 禁止组件根目录内——AppImage 只读挂载点写入必失败（旧事故「随包模型死重」同款）
    models_dir: Path = DEFAULT_MODELS_DIR
    default_model: str = "fun-asr-nano"
    #: 日志目录。默认 XDG 状态目录（契约库唯一出处，理由同 models_dir）
    log_dir: Path = DEFAULT_LOG_DIR

    models: dict[str, ModelEntry] = DEFAULT_MODEL_REGISTRY
    infer_timeout_s: float = DEFAULT_INFER_TIMEOUT_S
    queue_max_pending: int = DEFAULT_QUEUE_MAX_PENDING
    max_connections: int = DEFAULT_MAX_CONNECTIONS

    #: 托盘状态轮询间隔（毫秒）。状态转换为人感知秒级，500ms 刷新足够灵敏且零压力
    tray_poll_interval_ms: int = Field(default=500, ge=100)

    #: 是否启用托盘。False 强制纯控制台模式（服务器/CI 部署用）；
    #: True 时若检测到无显示环境仍自动降级（见 main.py）
    tray_enabled: bool = True

    @model_validator(mode="after")
    def _check_default_model_registered(self) -> "Settings":
        """启动校验：默认模型必须在注册表内，🔴 不存在即报错（禁止静默回退）。"""
        if self.default_model not in self.models:
            raise ValueError(
                f"default_model={self.default_model!r} 不在模型注册表中"
                f"（已注册: {sorted(self.models)}）"
            )
        return self
