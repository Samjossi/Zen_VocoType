"""chunk 辅助模块单元测试（audio_chunk 流式通道，v1.4）。

覆盖：三阶段构造、session_id 生成/校验、parse_chunk 结构校验全路径
（phase 未知、session_id 非法、seq 缺失/非法、total_bytes 非法、bool 排除）。
"""

import pytest

from zen_vocotype_protocol.chunk import (
    ALL_PHASES,
    PHASE_BEGIN,
    PHASE_DATA,
    PHASE_END,
    ChunkError,
    build_chunk_begin,
    build_chunk_data,
    build_chunk_end,
    is_valid_session_id,
    new_session_id,
    parse_chunk,
)


class TestSessionId:
    def test_new_session_id_roundtrip(self):
        assert is_valid_session_id(new_session_id())

    def test_rejects_non_string(self):
        for bad in (None, 123, b"abc", ["x"], {"x": 1}):
            assert not is_valid_session_id(bad)

    def test_rejects_non_uuid_string(self):
        for bad in ("", "not-a-uuid", "12345", "zzzz-0000"):
            assert not is_valid_session_id(bad)


class TestBuild:
    def test_begin_minimal(self):
        sid = new_session_id()
        assert build_chunk_begin(sid) == {"phase": PHASE_BEGIN, "session_id": sid}

    def test_begin_with_total_bytes(self):
        sid = new_session_id()
        chunk = build_chunk_begin(sid, total_bytes=1024)
        assert chunk["total_bytes"] == 1024

    def test_begin_rejects_bad_total_bytes(self):
        sid = new_session_id()
        for bad in (-1, 1.5, "1024", True):
            with pytest.raises(ChunkError):
                build_chunk_begin(sid, total_bytes=bad)

    def test_data(self):
        sid = new_session_id()
        assert build_chunk_data(sid, 0) == {
            "phase": PHASE_DATA,
            "session_id": sid,
            "seq": 0,
        }

    def test_data_rejects_bad_seq(self):
        sid = new_session_id()
        for bad in (-1, 0.5, "0", True, None):
            with pytest.raises(ChunkError):
                build_chunk_data(sid, bad)

    def test_end(self):
        sid = new_session_id()
        assert build_chunk_end(sid) == {"phase": PHASE_END, "session_id": sid}

    def test_build_rejects_bad_session_id(self):
        with pytest.raises(ChunkError):
            build_chunk_begin("not-a-uuid")
        with pytest.raises(ChunkError):
            build_chunk_data(None, 0)
        with pytest.raises(ChunkError):
            build_chunk_end(123)


class TestParseChunk:
    def test_parse_begin_roundtrip(self):
        chunk = build_chunk_begin(new_session_id(), total_bytes=2048)
        parsed = parse_chunk(chunk)
        assert parsed == chunk

    def test_parse_begin_without_total_bytes(self):
        chunk = build_chunk_begin(new_session_id())
        parsed = parse_chunk(chunk)
        assert "total_bytes" not in parsed

    def test_parse_data_roundtrip(self):
        chunk = build_chunk_data(new_session_id(), 7)
        assert parse_chunk(chunk) == chunk

    def test_parse_end_roundtrip(self):
        chunk = build_chunk_end(new_session_id())
        assert parse_chunk(chunk) == chunk

    def test_rejects_non_dict(self):
        for bad in (None, "x", 1, ["phase"]):
            with pytest.raises(ChunkError):
                parse_chunk(bad)

    def test_rejects_unknown_phase(self):
        with pytest.raises(ChunkError):
            parse_chunk({"phase": "middle", "session_id": new_session_id()})

    def test_rejects_missing_phase(self):
        with pytest.raises(ChunkError):
            parse_chunk({"session_id": new_session_id()})

    def test_rejects_bad_session_id(self):
        with pytest.raises(ChunkError):
            parse_chunk({"phase": PHASE_BEGIN, "session_id": "bad"})

    def test_data_requires_seq(self):
        with pytest.raises(ChunkError):
            parse_chunk({"phase": PHASE_DATA, "session_id": new_session_id()})

    def test_data_rejects_bad_seq(self):
        sid = new_session_id()
        for bad in (-1, 0.5, "0", True):
            with pytest.raises(ChunkError):
                parse_chunk({"phase": PHASE_DATA, "session_id": sid, "seq": bad})

    def test_begin_rejects_bad_total_bytes(self):
        sid = new_session_id()
        for bad in (-1, 1.5, "1", False):
            with pytest.raises(ChunkError):
                parse_chunk(
                    {"phase": PHASE_BEGIN, "session_id": sid, "total_bytes": bad}
                )

    def test_unknown_fields_dropped(self):
        """规范化副本仅含协议字段（多余字段不携带，防下游误读）。"""
        sid = new_session_id()
        parsed = parse_chunk(
            {"phase": PHASE_END, "session_id": sid, "extra": "drop-me"}
        )
        assert parsed == {"phase": PHASE_END, "session_id": sid}

    def test_all_phases_covered(self):
        """阶段常量全集与构造函数一一对应（防新增阶段漏改）。"""
        assert ALL_PHASES == {PHASE_BEGIN, PHASE_DATA, PHASE_END}
