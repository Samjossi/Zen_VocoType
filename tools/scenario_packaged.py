"""打包产物场景脚本（阶段 4 选型八层次 2：幂等 / 崩溃回收）。

场景 A「幂等二次执行」：Launcher 冷启动 → 再次执行 Launcher，
  期望经实例识别复用既有两端、快速返回退出码 0（🔴 禁止双开）。
场景 B「崩溃回收」：kill -9 服务端载荷进程 → 再次执行 Launcher，
  期望识别陈旧锁/Socket、重新拉起并就绪、退出码 0。

慢启动场景由真实模型加载天然覆盖（T2 等待即慢启动路径，见 T4.7 数据）。

用法：``.venv/bin/python tools/scenario_packaged.py``（结果 JSON 落 .temp/阶段4验收/）
"""

from __future__ import annotations

import json
import os
import re
import signal
import subprocess
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
LAUNCHER = PROJECT_ROOT / "dist" / "Zen_VocoType_Launcher.AppImage"
OUT_DIR = PROJECT_ROOT / ".temp" / "阶段4验收"

# 与契约库 paths 同回退约定（🔴 禁止 /tmp 可预测路径）
RUNTIME_DIR = Path(os.environ.get("XDG_RUNTIME_DIR", str(Path.home() / ".local" / "run")))
SERVICE_LOCK = RUNTIME_DIR / "zen_vocotype_service.lock"
CLIENT_LOCK = RUNTIME_DIR / "zen_vocotype_client.lock"
SOCKET = RUNTIME_DIR / "zen_vocotype.sock"


def _lock_pid(path: Path) -> int | None:
    try:
        text = path.read_text().strip()
    except OSError:
        return None
    m = re.search(r"\d+", text)
    return int(m.group()) if m else None


def _alive(pid: int | None) -> bool:
    if pid is None:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _sigterm_from_locks() -> None:
    for lock in (SERVICE_LOCK, CLIENT_LOCK):
        pid = _lock_pid(lock)
        if pid and _alive(pid):
            os.kill(pid, signal.SIGTERM)
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline and SOCKET.exists():
        time.sleep(0.5)


def _wait_locks_alive(timeout: float = 30) -> tuple[int | None, int | None]:
    """等待两端锁文件写入且进程存活（AppImage 挂载 + Qt 初始化需秒级，
    Launcher 返回时子进程锁尚未落盘——过早读锁会把「未写」误判为「未存活」）。"""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        svc, cli = _lock_pid(SERVICE_LOCK), _lock_pid(CLIENT_LOCK)
        if _alive(svc) and _alive(cli):
            return svc, cli
        time.sleep(0.5)
    return _lock_pid(SERVICE_LOCK), _lock_pid(CLIENT_LOCK)


def _run_launcher(timeout: float = 300) -> tuple[int, float]:
    start = time.monotonic()
    proc = subprocess.run(
        [str(LAUNCHER)], capture_output=True, text=True,
        timeout=timeout, cwd=PROJECT_ROOT / "dist",
    )
    return proc.returncode, time.monotonic() - start


def main() -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    report: dict = {"scenarios": []}

    try:
        # ------------------------------------------------ 场景 A：幂等二次执行
        _sigterm_from_locks()  # 冷态起步（幂等清理）
        code1, t1 = _run_launcher()
        assert code1 == 0, f"首次启动退出码 {code1}"
        svc_pid, cli_pid = _wait_locks_alive()
        assert _alive(svc_pid) and _alive(cli_pid), (
            f"首次启动后两端未存活（svc={svc_pid} cli={cli_pid}）"
        )
        code2, t2 = _run_launcher(timeout=60)
        assert code2 == 0, f"二次执行退出码 {code2}"
        assert _lock_pid(SERVICE_LOCK) == svc_pid, "二次执行双开了服务端"
        assert _lock_pid(CLIENT_LOCK) == cli_pid, "二次执行双开了客户端"
        report["scenarios"].append({
            "name": "A 幂等二次执行",
            "first_exit": code1, "first_s": round(t1, 2),
            "second_exit": code2, "second_s": round(t2, 2),
            "verdict": "PASS",
        })
        print(f"[场景A] 首启 {t1:.1f}s exit=0；二次 {t2:.1f}s exit=0（复用无双开）PASS", flush=True)

        # ------------------------------------------------ 场景 B：崩溃回收
        assert svc_pid is not None
        os.kill(svc_pid, signal.SIGKILL)  # 模拟崩溃（🔴 非优雅退出）
        time.sleep(1.0)
        assert not _alive(svc_pid), "kill -9 未生效"
        code3, t3 = _run_launcher(timeout=300)
        assert code3 == 0, f"崩溃回收启动退出码 {code3}"
        new_svc_pid, _ = _wait_locks_alive()
        assert _alive(new_svc_pid) and new_svc_pid != svc_pid, "服务端未被重新拉起"
        report["scenarios"].append({
            "name": "B 崩溃回收（kill -9 后重启）",
            "exit": code3, "s": round(t3, 2),
            "old_pid": svc_pid, "new_pid": new_svc_pid,
            "verdict": "PASS",
        })
        print(f"[场景B] kill -9 pid={svc_pid} → 重拉 pid={new_svc_pid}，{t3:.1f}s exit=0 PASS", flush=True)
    except Exception as exc:
        report["scenarios"].append({"name": "中断", "verdict": "FAIL", "error": str(exc)})
        print(f"[场景] FAIL: {exc}", flush=True)
    finally:
        _sigterm_from_locks()

    out = OUT_DIR / f"t48_scenarios_{int(time.time())}.json"
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[场景] 结果落盘 {out}")
    ok = all(s.get("verdict") == "PASS" for s in report["scenarios"])
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
