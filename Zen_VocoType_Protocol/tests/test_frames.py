"""frames 复合帧编解码单元测试（T1.1）。

覆盖：编解码往返、空体帧、粘包、拆包、头/体超限拒绝、JSON 头非法、
audio_bytes 声明一致性。
"""

import json

import pytest

from zen_vocotype_protocol.frames import (
    HEADER_LEN_BYTES,
    MAX_BODY_BYTES,
    MAX_HEADER_BYTES,
    FrameError,
    MessageBuffer,
    encode_frame,
)


class TestEncodeFrame:
    def test_roundtrip_with_body(self):
        header = {"action": "recognize", "audio_bytes": 6}
        body = b"\x01\x02\x03\x04\x05\x06"
        frame = encode_frame(header, body)
        buf = MessageBuffer()
        buf.feed(frame)
        got_header, got_body = buf.next_frame()
        assert got_header == header
        assert got_body == body

    def test_roundtrip_empty_body(self):
        header = {"action": "health"}
        buf = MessageBuffer()
        buf.feed(encode_frame(header))
        assert buf.next_frame() == (header, b"")

    def test_body_length_mismatch_rejected(self):
        with pytest.raises(FrameError):
            encode_frame({"action": "recognize", "audio_bytes": 10}, b"short")

    def test_body_without_declaration_rejected(self):
        with pytest.raises(FrameError):
            encode_frame({"action": "recognize"}, b"undeclared")

    def test_oversize_body_rejected(self):
        with pytest.raises(FrameError) as exc_info:
            encode_frame(
                {"action": "recognize", "audio_bytes": MAX_BODY_BYTES + 1},
                b"\x00" * (MAX_BODY_BYTES + 1),
            )
        assert exc_info.value.fatal

    def test_oversize_header_rejected(self):
        big_header = {"action": "x", "pad": "y" * MAX_HEADER_BYTES}
        with pytest.raises(FrameError) as exc_info:
            encode_frame(big_header)
        assert exc_info.value.fatal


class TestMessageBuffer:
    def test_incomplete_returns_none(self):
        buf = MessageBuffer()
        buf.feed(b"\x00\x00")
        assert buf.next_frame() is None

    def test_sticky_packets_two_frames_one_stream(self):
        """粘包：两帧一字节流，逐帧取出。"""
        f1 = encode_frame({"action": "health"})
        f2 = encode_frame({"action": "ready"})
        buf = MessageBuffer()
        buf.feed(f1 + f2)
        assert buf.next_frame()[0]["action"] == "health"
        assert buf.next_frame()[0]["action"] == "ready"
        assert buf.next_frame() is None

    def test_split_packet_feed_in_chunks(self):
        """拆包：一帧分多次 feed。"""
        frame = encode_frame({"action": "recognize", "audio_bytes": 4}, b"pcm!")
        buf = MessageBuffer()
        for chunk in (frame[:3], frame[3:7], frame[7:]):
            assert buf.next_frame() is None
            buf.feed(chunk)
        header, body = buf.next_frame()
        assert header["action"] == "recognize"
        assert body == b"pcm!"

    def test_byte_by_byte_feed(self):
        frame = encode_frame({"action": "health"})
        buf = MessageBuffer()
        result = None
        for i, byte in enumerate(frame):
            buf.feed(bytes([byte]))
            result = buf.next_frame()
            if i < len(frame) - 1:
                assert result is None
        assert result is not None
        assert result[0]["action"] == "health"

    def test_oversize_header_len_rejected_fatal(self):
        buf = MessageBuffer()
        buf.feed((MAX_HEADER_BYTES + 1).to_bytes(HEADER_LEN_BYTES, "big"))
        with pytest.raises(FrameError) as exc_info:
            buf.next_frame()
        assert exc_info.value.fatal

    def test_oversize_body_len_rejected_fatal(self):
        header = json.dumps(
            {"action": "recognize", "audio_bytes": MAX_BODY_BYTES + 1}
        ).encode()
        buf = MessageBuffer()
        buf.feed(len(header).to_bytes(HEADER_LEN_BYTES, "big") + header)
        with pytest.raises(FrameError) as exc_info:
            buf.next_frame()
        assert exc_info.value.fatal

    def test_invalid_json_header_nonfatal_and_recoverable(self):
        """JSON 头非法：抛非致命错误且缓冲已丢弃坏帧，后续帧可正常解析。"""
        bad = b"not-json!!"
        buf = MessageBuffer()
        buf.feed(len(bad).to_bytes(HEADER_LEN_BYTES, "big") + bad)
        with pytest.raises(FrameError) as exc_info:
            buf.next_frame()
        assert not exc_info.value.fatal
        # 后续正常帧不受影响
        buf.feed(encode_frame({"action": "health"}))
        assert buf.next_frame()[0]["action"] == "health"

    def test_non_object_json_header_rejected(self):
        raw = json.dumps([1, 2, 3]).encode()
        buf = MessageBuffer()
        buf.feed(len(raw).to_bytes(HEADER_LEN_BYTES, "big") + raw)
        with pytest.raises(FrameError):
            buf.next_frame()

    def test_invalid_audio_bytes_field_rejected_and_recoverable(self):
        raw = json.dumps({"action": "recognize", "audio_bytes": -5}).encode()
        buf = MessageBuffer()
        buf.feed(len(raw).to_bytes(HEADER_LEN_BYTES, "big") + raw)
        with pytest.raises(FrameError) as exc_info:
            buf.next_frame()
        assert not exc_info.value.fatal
        buf.feed(encode_frame({"action": "health"}))
        assert buf.next_frame()[0]["action"] == "health"

    def test_audio_bytes_none_means_no_body(self):
        raw = json.dumps({"action": "health", "audio_bytes": None}).encode()
        buf = MessageBuffer()
        buf.feed(len(raw).to_bytes(HEADER_LEN_BYTES, "big") + raw)
        header, body = buf.next_frame()
        assert body == b""

    def test_feed_empty_is_noop(self):
        buf = MessageBuffer()
        buf.feed(b"")
        assert buf.next_frame() is None
