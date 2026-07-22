"""服务端配置（单一配置源，选型 7 定稿）。

每组件恰好一个 ``Settings`` 类 + 一个 ``config.yaml`` + 环境变量覆盖：

- 配置文件：组件根目录 ``config.yaml``（位置推算见契约库 ``settings.component_root``）
- 环境变量前缀：``ZEN_VOCOTYPE_SERVICE_``
- 配置源优先级与组件根推算的**行为逻辑单一出处**为契约库
  ``zen_vocotype_protocol.settings``，本文件仅声明字段与默认值
- Socket 路径默认值唯一出处为契约库 ``zen_vocotype_protocol.paths``，此处仅允许覆盖
- 模型注册表内嵌于本配置（选型二），缺省内置 paraformer-large、sensevoice-small
  与 seaco-paraformer 三条
"""

from pathlib import Path

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
#: CPU 推理耗时实测标定见阶段 1 验收记录（T1.6），当前值为其安全上界
DEFAULT_INFER_TIMEOUT_S: float = 60.0

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
DEFAULT_MODEL_REGISTRY: dict[str, dict] = {
    "paraformer-large": {
        "model_id": "iic/speech_paraformer-large-vad-punc_asr_nat-zh-cn-16k-common-vocab8404-pytorch",
        "vad_model_id": "iic/speech_fsmn_vad_zh-cn-16k-common-pytorch",
        "punc_model_id": "iic/punc_ct-transformer_zh-cn-common-vocab272727-pytorch",
        "description": "通用中文离线识别（默认）。非自回归 Paraformer-large，"
        "集成 VAD/标点/时间戳，支持数小时长音频，中文公开数据集 SOTA 级。",
    },
    "sensevoice-small": {
        "model_id": "iic/SenseVoiceSmall",
        "vad_model_id": "iic/speech_fsmn_vad_zh-cn-16k-common-pytorch",
        "description": "多语言语音理解：中/粤/英/日/韩等 50+ 语种，"
        "兼具情感识别与声音事件检测；推理极快（10 秒音频约 70ms）。",
    },
    "seaco-paraformer": {
        "model_id": "iic/speech_seaco_paraformer_large_asr_nat-zh-cn-16k-common-vocab8404-pytorch",
        "vad_model_id": "iic/speech_fsmn_vad_zh-cn-16k-common-pytorch",
        "punc_model_id": "iic/punc_ct-transformer_zh-cn-common-vocab272727-pytorch",
        "description": "热词定制识别（ICASSP 2024）。SeACo-Paraformer 通过后验概率"
        "融合激励热词，提升专有名词/术语的召回与准确率；不传热词时即通用中文识别。",
    },
}


class Settings(ComponentSettings):
    """服务端全部配置项的唯一入口。"""

    model_config = component_model_config(__file__, "ZEN_VOCOTYPE_SERVICE_")

    socket_path: str = DEFAULT_SOCKET_PATH
    #: 模型根目录（MODELSCOPE_CACHE 指向）。默认 XDG 数据目录（契约库唯一出处），
    #: 🔴 禁止组件根目录内——AppImage 只读挂载点写入必失败（旧事故「随包模型死重」同款）
    models_dir: Path = DEFAULT_MODELS_DIR
    default_model: str = "paraformer-large"
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
