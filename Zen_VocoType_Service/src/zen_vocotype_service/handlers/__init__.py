"""action 处理器包：每 action 一个处理器函数，经 ``dispatch`` 路由。

处理器签名::

    handler(header: dict, body: bytes, ctx: ServiceContext) -> dict  # 返回 payload

- 业务错误抛 ``ProtocolError(code, message)``，分发层转为协议错误响应
- 🔴 任何未预期异常也会被分发层捕获为 4002/500 系错误响应，禁止连接悬挂
- ``audio_chunk`` 例外：签名多一个 ``owner``（连接令牌，会话绑定连接用），
  由 ``connection.dispatch`` 特判传入（v1.4 起实现，1005 分支对其自然失效）
"""

from zen_vocotype_service.context import ServiceContext
from zen_vocotype_service.handlers import (
    audio_chunk,
    health,
    model_info,
    model_switch,
    ready,
    recognize,
)

#: action → 处理器路由表（audio_chunk 亦在表内：入站校验放行，
#: dispatch 对其特判传 owner；未来新增预留 action 不入表即返回 1005）
HANDLERS = {
    "health": health.handle,
    "ready": ready.handle,
    "recognize": recognize.handle,
    "model_info": model_info.handle,
    "model_switch": model_switch.handle,
    "audio_chunk": audio_chunk.handle,
}

__all__ = ["HANDLERS", "ServiceContext"]
