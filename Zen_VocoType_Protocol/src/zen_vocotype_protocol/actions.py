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

#: 流式识别音频分片（v1.4 起实现）：begin → N×data → end 三阶段会话，
#: chunk 对象语义见契约库 ``chunk.py``（构造/校验单一出处）
ACTION_AUDIO_CHUNK: str = "audio_chunk"

#: 全部已定义 action（供校验入站请求合法性）。
#: 入站校验应放行已定义 action，由分发层对本端未实现者返回
#: ``errors.ERR_ACTION_NOT_SUPPORTED``（1005）；
#: 只有不在本集合中的 action 才返回 ``errors.ERR_UNKNOWN_ACTION``（1002）
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
