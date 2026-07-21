"""协议响应构建与处理器异常约定。

响应形态（协议 §2.2）：

- 成功：``ok: true`` + ``payload``
- 失败：``ok: false`` + ``error.code`` / ``error.message``
- ``request_id`` 必须回显请求方的值
"""

from typing import Any

from zen_vocotype_protocol.version import PROTOCOL_VERSION


class ProtocolError(Exception):
    """处理器业务错误：携带协议错误码与真实原因，由分发层转为错误响应。"""

    def __init__(self, code: int, message: str) -> None:
        super().__init__(message)
        self.code: int = code
        self.message: str = message


def build_response(
    request_header: dict[str, Any],
    *,
    ok: bool,
    payload: dict[str, Any] | None = None,
    error_code: int | None = None,
    error_message: str | None = None,
) -> dict[str, Any]:
    """构建协议响应头（回显 request_id / action，附服务端协议版本）。"""
    header: dict[str, Any] = {
        "action": request_header.get("action"),
        "protocol_version": PROTOCOL_VERSION,
        "request_id": request_header.get("request_id"),
        "ok": ok,
    }
    if ok:
        header["payload"] = payload if payload is not None else {}
    else:
        header["error"] = {"code": error_code, "message": error_message}
        if payload:
            header["payload"] = payload
    return header
