"""action 常量全集。

协议语义详见 ``文档/通信协议设计_v1.0.md``；常量以本模块为单一出处，
🔴 禁止各端自行重复定义（旧 GridChat 两端各定义一份导致漂移为反面案例）。
"""

# ---------------------------------------------------------------------------
# 已定义 action（v1）
# ---------------------------------------------------------------------------

#: 健康检查：返回服务状态、协议版本、模型是否加载完成
ACTION_HEALTH: str = "health"

#: 协议级就绪确认：就绪 = 模型加载完成、可识别（语义区别于「Socket 可连接」）
ACTION_READY: str = "ready"

#: 音频识别：请求携带 PCM 音频体，返回文本/置信度/时长
ACTION_RECOGNIZE: str = "recognize"

#: 查询当前模型与可用模型列表
ACTION_MODEL_INFO: str = "model_info"

#: 切换模型（服务端必须真实分发到对应模型，🔴 禁止旧版假切换）
ACTION_MODEL_SWITCH: str = "model_switch"

#: 流式识别音频分片（v1 仅预留帧复用能力，🔴 不实现）
ACTION_AUDIO_CHUNK: str = "audio_chunk"

#: 全部已定义 action（供校验入站请求合法性）
ALL_ACTIONS: frozenset[str] = frozenset(
    {
        ACTION_HEALTH,
        ACTION_READY,
        ACTION_RECOGNIZE,
        ACTION_MODEL_INFO,
        ACTION_MODEL_SWITCH,
        ACTION_AUDIO_CHUNK,
    }
)
