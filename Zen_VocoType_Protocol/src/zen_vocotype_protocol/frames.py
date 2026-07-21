"""复合帧编解码（骨架）。

帧结构（选型 6 定稿，详见 ``文档/通信协议设计_v1.0.md``）::

    [4 字节大端头长度][JSON 头（UTF-8）][二进制体（可为空）]

- 头长度 = JSON 头字节数（不含自身 4 字节、不含二进制体）
- JSON 头必含 ``action`` 字段；携带音频时以 ``audio_bytes`` 声明体长度
- 粘包处理沿用旧项目验证可用的 MessageBuffer 思路（重新实现，不复制）

🔴 本模块当前为骨架：仅定义结构常量与接口签名，编解码实现属阶段 1 协议层工作。
"""

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


def encode_frame(header: dict, body: bytes = b"") -> bytes:
    """将 JSON 头与二进制体编码为一帧字节流。

    :param header: JSON 头字典，必含 ``action``；携带体时必须声明 ``audio_bytes``
    :param body: 二进制体（如原始 PCM），可为空
    :raises NotImplementedError: 骨架阶段，实现属阶段 1
    """
    raise NotImplementedError("阶段 1 实现：协议层编解码")


class MessageBuffer:
    """粘包处理缓冲（骨架）。

    沿用旧项目验证可用的思路：持续 ``feed`` 收到的字节流，
    反复 ``next_frame`` 取出完整帧；不足一帧时返回 ``None`` 等待更多数据。
    """

    def feed(self, data: bytes) -> None:
        """追加收到的字节流。"""
        raise NotImplementedError("阶段 1 实现：协议层编解码")

    def next_frame(self) -> tuple[dict, bytes] | None:
        """取出一个完整帧；缓冲不足一帧时返回 ``None``。"""
        raise NotImplementedError("阶段 1 实现：协议层编解码")
