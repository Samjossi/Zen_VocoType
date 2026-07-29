"""复合帧编解码。

帧结构（详见 ``文档/通信协议设计_v1.0.md`` §2）::

    [4 字节大端头长度][JSON 头（UTF-8）][二进制体（可为空）]

- 头长度 = JSON 头字节数（不含自身 4 字节、不含二进制体）
- JSON 头必含 ``action`` 字段；携带音频时以 ``audio_bytes`` 声明体长度
- 粘包处理沿用旧项目验证可用的 MessageBuffer 思路（重新实现，不复制）

防御性上限（协议 §2 / §7-1）：头超 ``MAX_HEADER_BYTES``、体超
``MAX_BODY_BYTES`` 即判帧损坏，抛出 ``FrameError``，由服务端捕获后
记录错误并关闭连接（🔴 禁止静默截断或容错续传）。
"""

import json

#: 头长度前缀字节数
HEADER_LEN_BYTES: int = 4

#: 头长度前缀字节序（大端，沿用旧设计）
HEADER_BYTEORDER: str = "big"

#: JSON 头编码
HEADER_ENCODING: str = "utf-8"

#: 单帧 JSON 头最大字节数（防御性上限，超过即判恶意/损坏并断连）
MAX_HEADER_BYTES: int = 64 * 1024

#: 单帧二进制体最大字节数（16kHz/16bit/单声道 ≈ 32KB/s，上限约合 10 分钟录音）
MAX_BODY_BYTES: int = 32 * 1024 * 600

#: 响应方向 JSON 头放宽上限（v1.4 新增）。识别文本在响应头 payload 内，
#: 长音频文本体积可观（实测 31 分钟中文识别 ≈ 1.05 万字 ≈ 31KB UTF-8，
#: 2 小时 ≈ 4 万字 ≈ 123KB，超请求方向 64KB 上限）；本值 4MB 留 >30 倍余量。
#: ⚠️ 方向语义：服务端解析请求、客户端编码请求仍用 ``MAX_HEADER_BYTES``（64KB
#: 防御不变）；服务端编码响应、客户端解析响应用本值（响应为服务端自产，
#: 上限语义是防失控兜底而非防恶意）
MAX_RESPONSE_HEADER_BYTES: int = 4 * 1024 * 1024


class FrameError(Exception):
    """帧编解码/解析失败的统一异常。

    ``fatal=True`` 表示连接级损坏（头/体超限），调用方必须关闭连接；
    ``fatal=False`` 表示单帧级错误（如 JSON 非法），调用方按协议返回
    错误码后可继续处理后续帧。
    """

    def __init__(self, message: str, *, fatal: bool = False) -> None:
        super().__init__(message)
        self.fatal: bool = fatal


def encode_frame(header: dict, body: bytes = b"", *, max_header_bytes: int = MAX_HEADER_BYTES) -> bytes:
    """将 JSON 头与二进制体编码为一帧字节流。

    :param header: JSON 头字典；携带体时必须声明 ``audio_bytes`` 且与实际体长一致
    :param body: 二进制体（如原始 PCM），可为空
    :param max_header_bytes: 头上限（默认请求方向 64KB；服务端编码识别响应时
        传 ``MAX_RESPONSE_HEADER_BYTES``——长音频文本可超 64KB）
    :raises FrameError: 头/体超防御性上限，或头声明的 ``audio_bytes`` 与体长不符
    """
    if body:
        declared = header.get("audio_bytes")
        if declared != len(body):
            raise FrameError(
                f"头声明 audio_bytes={declared!r} 与实际体长 {len(body)} 不符"
            )
    header_bytes = json.dumps(header, ensure_ascii=False).encode(HEADER_ENCODING)
    if len(header_bytes) > max_header_bytes:
        raise FrameError(
            f"JSON 头 {len(header_bytes)} 字节超上限 {max_header_bytes}", fatal=True
        )
    if len(body) > MAX_BODY_BYTES:
        raise FrameError(
            f"二进制体 {len(body)} 字节超上限 {MAX_BODY_BYTES}", fatal=True
        )
    return len(header_bytes).to_bytes(HEADER_LEN_BYTES, HEADER_BYTEORDER) + header_bytes + body


class MessageBuffer:
    """粘包处理缓冲。

    持续 ``feed`` 收到的字节流，反复 ``next_frame`` 取出完整帧；
    不足一帧时返回 ``None`` 等待更多数据。

    🔴 每条连接必须持有独立实例（协议 §7-3），禁止跨连接共享。

    :param max_header_bytes: 头上限（默认请求方向 64KB；客户端解析服务端
        识别响应时传 ``MAX_RESPONSE_HEADER_BYTES``——长音频文本可超 64KB）
    """

    def __init__(self, max_header_bytes: int = MAX_HEADER_BYTES) -> None:
        self._buf: bytearray = bytearray()
        self._max_header_bytes = max_header_bytes

    def feed(self, data: bytes) -> None:
        """追加收到的字节流。"""
        if data:
            self._buf.extend(data)

    def next_frame(self) -> tuple[dict, bytes] | None:
        """取出一个完整帧；缓冲不足一帧时返回 ``None``。

        :raises FrameError: 头/体超防御性上限（``fatal=True``），或 JSON 头非法
        """
        if len(self._buf) < HEADER_LEN_BYTES:
            return None

        header_len = int.from_bytes(self._buf[:HEADER_LEN_BYTES], HEADER_BYTEORDER)
        if header_len > self._max_header_bytes:
            raise FrameError(
                f"JSON 头长度 {header_len} 超上限 {self._max_header_bytes}", fatal=True
            )
        if len(self._buf) < HEADER_LEN_BYTES + header_len:
            return None

        header_raw = bytes(self._buf[HEADER_LEN_BYTES : HEADER_LEN_BYTES + header_len])
        try:
            header = json.loads(header_raw.decode(HEADER_ENCODING))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            # 头已完整收到但内容非法：丢弃该头字节，保证后续帧可继续解析
            del self._buf[: HEADER_LEN_BYTES + header_len]
            raise FrameError(f"JSON 头解析失败: {exc}") from exc
        if not isinstance(header, dict):
            del self._buf[: HEADER_LEN_BYTES + header_len]
            raise FrameError(f"JSON 头不是对象: {type(header).__name__}")

        try:
            body_len = self._parse_body_len(header)
        except FrameError:
            # 头字段非法：丢弃该头字节，保证后续帧可继续解析
            del self._buf[: HEADER_LEN_BYTES + header_len]
            raise
        if body_len > MAX_BODY_BYTES:
            raise FrameError(
                f"二进制体长度 {body_len} 超上限 {MAX_BODY_BYTES}", fatal=True
            )
        frame_end = HEADER_LEN_BYTES + header_len + body_len
        if len(self._buf) < frame_end:
            return None

        body = bytes(self._buf[HEADER_LEN_BYTES + header_len : frame_end])
        del self._buf[:frame_end]
        return header, body

    @staticmethod
    def _parse_body_len(header: dict) -> int:
        """从头字段 ``audio_bytes`` 解析体长度；缺失/为 0 视为无体。"""
        raw = header.get("audio_bytes", 0)
        if raw is None:
            return 0
        if not isinstance(raw, int) or isinstance(raw, bool) or raw < 0:
            raise FrameError(f"audio_bytes 字段非法: {raw!r}")
        return raw
