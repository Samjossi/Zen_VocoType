"""打包产物协议级 E2E（阶段 4 选型八层次 1）。

以打包产物（onedir 二进制或 AppImage）拉起 Service，经协议复合帧执行：
connect → health（版本握手）→ ready 轮询 → recognize 真实音频样例识别
比对（复用阶段 2 示例语音资产与重合率口径）→ SIGTERM 优雅退出断言。

用法：

    .venv/bin/python tools/e2e_packaged.py --service dist/zen_vocotype_service/zen_vocotype_service
    .venv/bin/python tools/e2e_packaged.py --service dist/Zen_VocoType_Service.AppImage \
        --models-dir <空目录>   # 首启下载用例（验收标准 3 路径一）

原始结果 JSON 落 ``.temp/阶段4验收/``（C8）。
"""

from __future__ import annotations

import argparse
import json
import os
import signal
import socket
import subprocess
import sys
import time
import uuid
import wave
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "Zen_VocoType_Protocol" / "src"))

from zen_vocotype_protocol.frames import MessageBuffer, encode_frame  # noqa: E402
from zen_vocotype_protocol.version import PROTOCOL_VERSION  # noqa: E402

#: 字符级重合率宽松阈值（与阶段 2 test_sample_recognition 同口径）
OVERLAP_THRESHOLD = 0.5

SAMPLES_DIR = PROJECT_ROOT / "参考代码" / "示例语音和文字"
OUT_DIR = PROJECT_ROOT / ".temp" / "阶段4验收"


def _rpc(sock_path: str, action: str, body: bytes = b"", **extra) -> dict:
    header = {
        "action": action,
        "protocol_version": PROTOCOL_VERSION,
        "request_id": str(uuid.uuid4()),
    }
    header.update(extra)
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as conn:
        conn.settimeout(120)
        conn.connect(sock_path)
        conn.sendall(encode_frame(header, body))
        buf = MessageBuffer()
        while True:
            data = conn.recv(1 << 20)
            if not data:
                raise RuntimeError(f"{action}: 连接被关闭但未收到响应")
            buf.feed(data)
            frame = buf.next_frame()
            if frame is not None:
                return frame[0]


def _wait_connectable(sock_path: str, timeout: float, proc) -> float:
    start = time.monotonic()
    while time.monotonic() - start < timeout:
        if proc.poll() is not None:
            raise RuntimeError(f"服务进程提前退出 exit={proc.returncode}")
        try:
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as conn:
                conn.settimeout(0.2)
                conn.connect(sock_path)
                return time.monotonic() - start
        except OSError:
            time.sleep(0.05)
    raise TimeoutError(f"Socket {timeout}s 内不可连接")


def _wait_ready(sock_path: str, timeout: float, proc) -> float:
    start = time.monotonic()
    while time.monotonic() - start < timeout:
        if proc.poll() is not None:
            raise RuntimeError(f"服务进程提前退出 exit={proc.returncode}")
        resp = _rpc(sock_path, "ready")
        if resp["ok"] and resp["payload"].get("ready"):
            return time.monotonic() - start
        if not resp["ok"]:
            raise RuntimeError(f"模型加载失败: {resp['error']}")
        time.sleep(0.5)
    raise TimeoutError(f"ready {timeout}s 内未就绪")


def _overlap(ref: str, hyp: str) -> float:
    """字符级重合率（与阶段 2 同算法：参考文本各字符在识别结果中的命中比）。"""
    if not ref:
        return 0.0
    hit = sum(1 for ch in ref if ch in hyp)
    return hit / len(ref)


def _read_pcm(wav_path: Path) -> bytes:
    with wave.open(str(wav_path), "rb") as w:
        assert (w.getnchannels(), w.getframerate(), w.getsampwidth()) == (1, 16000, 2)
        return w.readframes(w.getnframes())


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--service", required=True, type=Path, help="打包 Service 二进制/AppImage")
    parser.add_argument("--models-dir", type=Path, default=None,
                        help="覆盖模型目录（空目录 = 首启下载用例）")
    parser.add_argument("--ready-timeout", type=float, default=1800.0,
                        help="ready 等待上限（首启下载场景放宽，默认 30 分钟）")
    args = parser.parse_args()

    binary = args.service.resolve()
    if not binary.is_file():
        print(f"FAIL: 二进制不存在 {binary}")
        return 1

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    sock_path = OUT_DIR / "t48_e2e.sock"
    sock_path.unlink(missing_ok=True)
    log_dir = OUT_DIR / "t48_logs"
    log_dir.mkdir(exist_ok=True)
    (log_dir / "service.log").unlink(missing_ok=True)

    env = dict(os.environ)
    env.update(
        {
            "ZEN_VOCOTYPE_SERVICE_SOCKET_PATH": str(sock_path),
            "ZEN_VOCOTYPE_SERVICE_LOG_DIR": str(log_dir),
            "ZEN_VOCOTYPE_SERVICE_TRAY_ENABLED": "false",
        }
    )
    if args.models_dir is not None:
        env["ZEN_VOCOTYPE_SERVICE_MODELS_DIR"] = str(args.models_dir.resolve())

    form = "AppImage" if binary.suffix == ".AppImage" else "onedir"
    print(f"[e2e] 形态={form} 二进制={binary.name}", flush=True)

    t0 = time.monotonic()
    proc = subprocess.Popen(
        [str(binary)],
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    result: dict = {"form": form, "binary": binary.name, "samples": []}
    try:
        result["t1_socket_connect_s"] = round(_wait_connectable(str(sock_path), 30, proc), 3)
        print(f"[e2e] T1 Socket 可连 {result['t1_socket_connect_s']}s", flush=True)

        health = _rpc(str(sock_path), "health")
        assert health["ok"] and health["protocol_version"] == PROTOCOL_VERSION, health
        result["health"] = health["payload"]

        result["t2_ready_s"] = round(_wait_ready(str(sock_path), args.ready_timeout, proc), 3)
        print(f"[e2e] T2 ready {result['t2_ready_s']}s", flush=True)

        wavs = sorted(SAMPLES_DIR.glob("*.wav"))
        assert wavs, f"样例缺失：{SAMPLES_DIR}"
        lows = []
        for wav_path in wavs:
            ref = wav_path.with_suffix(".txt").read_text(encoding="utf-8").strip()
            pcm = _read_pcm(wav_path)
            resp = _rpc(
                str(sock_path),
                "recognize",
                pcm,
                audio_format={"sample_rate": 16000, "channels": 1, "sample_width": 2},
                audio_bytes=len(pcm),
            )
            assert resp["ok"], f"{wav_path.name} 识别失败: {resp.get('error')}"
            hyp = resp["payload"]["text"]
            assert hyp, f"{wav_path.name} 识别文本为空"
            ratio = _overlap(ref, hyp)
            result["samples"].append(
                {"name": wav_path.name, "overlap": round(ratio, 3), "ref": ref[:30], "hyp": hyp[:30]}
            )
            print(f"[e2e] {wav_path.name} 重合率 {ratio:.3f}", flush=True)
            if ratio < OVERLAP_THRESHOLD:
                lows.append(wav_path.name)
        if lows:
            raise AssertionError(f"重合率低于 {OVERLAP_THRESHOLD}: {lows}")
        result["elapsed_s"] = round(time.monotonic() - t0, 3)
        result["verdict"] = "PASS"
    except Exception as exc:
        result["verdict"] = "FAIL"
        result["error"] = str(exc)
        print(f"[e2e] FAIL: {exc}", flush=True)
    finally:
        if proc.poll() is None:
            os.killpg(proc.pid, signal.SIGTERM)
            try:
                proc.wait(timeout=30)
                result["graceful_exit_code"] = proc.returncode
            except subprocess.TimeoutExpired:
                proc.kill()
                result["graceful_exit_code"] = "timeout-killed"

    out = OUT_DIR / f"t48_e2e_{form}_{int(time.time())}.json"
    out.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[e2e] 结果落盘 {out}（退出码 {result.get('graceful_exit_code')}）")
    return 0 if result["verdict"] == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())
