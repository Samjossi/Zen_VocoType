"""``audio_chunk`` 流式通道 chunk 对象构造与校验（契约库单一出处）。

协议语义见协议设计文档 v1.1 §3.6：``begin`` → N×``data`` → ``end``
三阶段会话，复用「每请求帧必有一响应帧」的严格有序模型（响应即 ack）。

🔴 服务端与第三方接入方（如 AI_Video_Transcriber）必须经本模块构造/解析
chunk 对象，禁止各自实现导致漂移（旧两端各定义一份致漂移为反面案例）。

职责边界：本模块只校验**单帧 chunk 对象自身的结构合法性**；seq 连续性、
会话唯一性、总量上限等**跨帧状态校验**由服务端会话管理模块负责
（依赖其持有的会话表，无法也无须下沉契约库）。
"""

import uuid
from typing import Any

# ---------------------------------------------------------------------------
# 阶段常量
# ---------------------------------------------------------------------------

#: 会话开始：创建会话、（可选）预告总量，响应返回会话上限
PHASE_BEGIN: str = "begin"

#: 数据分片：携带 PCM 体与递增 seq，响应返回累计 received_bytes（进度反馈）
PHASE_DATA: str = "data"

#: 会话结束：触发整段识别，响应同 ``recognize`` payload
PHASE_END: str = "end"

#: 全部合法阶段值
ALL_PHASES: frozenset[str] = frozenset({PHASE_BEGIN, PHASE_DATA, PHASE_END})


class ChunkError(ValueError):
    """chunk 对象结构非法（消息面向日志与排障，写真实原因）。"""


# ---------------------------------------------------------------------------
# session_id
# ---------------------------------------------------------------------------


def new_session_id() -> str:
    """生成新会话 ID（UUID4 字符串，由客户端在 begin 时生成）。"""
    return str(uuid.uuid4())


def is_valid_session_id(value: Any) -> bool:
    """校验会话 ID 形态：UUID 字符串（🔴 非字符串/非 UUID 一律拒绝）。"""
    if not isinstance(value, str):
        return False
    try:
        uuid.UUID(value)
    except (ValueError, AttributeError):
        return False
    return True


# ---------------------------------------------------------------------------
# 构造（接入方/客户端侧）
# ---------------------------------------------------------------------------


def build_chunk_begin(session_id: str, total_bytes: int | None = None) -> dict:
    """构造 begin 阶段 chunk 对象。

    :param session_id: 客户端生成的会话 ID（``new_session_id`` 产物）
    :param total_bytes: 可选总量预告（服务端预校验手段，预告即超上限可在
        begin 阶段直接拒绝；缺失不报错）
    :raises ChunkError: 参数非法
    """
    if not is_valid_session_id(session_id):
        raise ChunkError(f"session_id 非法: {session_id!r}")
    chunk: dict[str, Any] = {"phase": PHASE_BEGIN, "session_id": session_id}
    if total_bytes is not None:
        _validate_non_negative_int("total_bytes", total_bytes)
        chunk["total_bytes"] = total_bytes
    return chunk


def build_chunk_data(session_id: str, seq: int) -> dict:
    """构造 data 阶段 chunk 对象（PCM 体经帧二进制体携带，不在 chunk 内）。

    :param seq: 分片序号，从 0 开始严格递增（连续性由服务端校验）
    :raises ChunkError: 参数非法
    """
    if not is_valid_session_id(session_id):
        raise ChunkError(f"session_id 非法: {session_id!r}")
    _validate_non_negative_int("seq", seq)
    return {"phase": PHASE_DATA, "session_id": session_id, "seq": seq}


def build_chunk_end(session_id: str) -> dict:
    """构造 end 阶段 chunk 对象。

    :raises ChunkError: 参数非法
    """
    if not is_valid_session_id(session_id):
        raise ChunkError(f"session_id 非法: {session_id!r}")
    return {"phase": PHASE_END, "session_id": session_id}


# ---------------------------------------------------------------------------
# 解析校验（服务端侧）
# ---------------------------------------------------------------------------


def parse_chunk(chunk: Any) -> dict:
    """解析并校验请求头中的 ``chunk`` 对象，返回规范化副本（仅含合法字段）。

    :param chunk: 请求头 ``chunk`` 字段原值
    :return: ``{"phase", "session_id"}`` + data 阶段 ``"seq"``
        + begin 阶段可选 ``"total_bytes"``
    :raises ChunkError: 结构非法（缺字段/phase 未知/session_id 非法/
        seq 非法/total_bytes 非法）
    """
    if not isinstance(chunk, dict):
        raise ChunkError(f"chunk 字段缺失或不是对象: {type(chunk).__name__}")
    phase = chunk.get("phase")
    if phase not in ALL_PHASES:
        raise ChunkError(f"chunk.phase 非法: {phase!r}")
    session_id = chunk.get("session_id")
    if not is_valid_session_id(session_id):
        raise ChunkError(f"chunk.session_id 非法: {session_id!r}")
    normalized: dict[str, Any] = {"phase": phase, "session_id": session_id}
    if phase == PHASE_DATA:
        seq = chunk.get("seq")
        if seq is None:
            raise ChunkError("data 阶段缺少 chunk.seq 字段")
        _validate_non_negative_int("seq", seq)
        normalized["seq"] = seq
    elif phase == PHASE_BEGIN and chunk.get("total_bytes") is not None:
        total_bytes = chunk["total_bytes"]
        _validate_non_negative_int("total_bytes", total_bytes)
        normalized["total_bytes"] = total_bytes
    return normalized


def _validate_non_negative_int(field: str, value: Any) -> None:
    """校验字段为非负 int（🔴 bool 是 int 子类，必须显式排除）。

    :raises ChunkError: 非法时抛出
    """
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise ChunkError(f"chunk.{field} 非法（须为非负整数）: {value!r}")
