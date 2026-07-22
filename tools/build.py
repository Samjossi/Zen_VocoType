#!/usr/bin/env python3
"""Zen_VocoType 统一构建入口（选型一方案 A / 选型二方案 A）。

用法：

- ``.venv/bin/python tools/build.py --component service``       构建 onedir
- ``.venv/bin/python tools/build.py --component client --appimage``  onedir + AppImage
- ``.venv/bin/python tools/build.py --component all``           三组件全量

约定（C8）：

- 产物输出至项目内 ``dist/``；构建临时产物落 ``./.temp/build/``
  （PyInstaller ``workpath``/``distpath`` 显式指定，🔴 禁止系统临时目录）
- spec 集中放 ``tools/specs/``，🔴 禁止各组件目录内再长私有打包脚本
- 构建后冒烟（产物存在、可执行、``--version`` 探针可跑）未过🔴 不进入封装
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

#: 项目根（tools/build.py → 上一级）
PROJECT_ROOT: Path = Path(__file__).resolve().parents[1]

#: 组件名 → （组件目录名, spec 文件, onedir 产物名）
COMPONENTS: dict[str, tuple[str, str, str]] = {
    "service": ("Zen_VocoType_Service", "zen_vocotype_service.spec", "zen_vocotype_service"),
    "client": ("Zen_VocoType_Client", "zen_vocotype_client.spec", "zen_vocotype_client"),
    "launcher": ("Zen_VocoType_Launcher", "zen_vocotype_launcher.spec", "zen_vocotype_launcher"),
}

#: AppImage 产物名模板（选型三：三独立 AppImage + 邻接目录布局）
APPIMAGE_NAMES: dict[str, str] = {
    "service": "Zen_VocoType_Service.AppImage",
    "client": "Zen_VocoType_Client.AppImage",
    "launcher": "Zen_VocoType_Launcher.AppImage",
}

DIST_DIR: Path = PROJECT_ROOT / "dist"
BUILD_DIR: Path = PROJECT_ROOT / ".temp" / "build"


def _run(cmd: list[str], **kwargs) -> subprocess.CompletedProcess:
    """子进程执行封装（实时回显，失败抛 CalledProcessError）。"""
    print(f"[build] $ {' '.join(str(c) for c in cmd)}", flush=True)
    return subprocess.run(cmd, check=True, **kwargs)


def build_onedir(component: str) -> Path:
    """构建单组件 onedir 产物，返回产物目录路径。"""
    _, spec_name, artifact = COMPONENTS[component]
    spec_path = PROJECT_ROOT / "tools" / "specs" / spec_name
    workpath = BUILD_DIR / component
    DIST_DIR.mkdir(parents=True, exist_ok=True)
    workpath.mkdir(parents=True, exist_ok=True)

    _run(
        [
            sys.executable,
            "-m",
            "PyInstaller",
            "--noconfirm",
            "--clean",
            "--workpath",
            str(workpath),
            "--distpath",
            str(DIST_DIR),
            str(spec_path),
        ],
        cwd=PROJECT_ROOT,
    )
    artifact_dir = DIST_DIR / artifact
    if not artifact_dir.is_dir():
        raise RuntimeError(f"onedir 产物缺失：{artifact_dir}")
    return artifact_dir


def smoke_check(component: str, artifact_dir: Path) -> None:
    """构建冒烟：二进制存在/可执行/--version 探针可跑 + 随包文件抽查。

    🔴 冒烟未过不得进入封装（选型一）；探针零写盘（各入口 --version
    在配置/日志初始化前返回）。
    """
    binary = artifact_dir / artifact_dir.name
    if not binary.is_file():
        raise RuntimeError(f"二进制缺失：{binary}")
    if not os.access(binary, os.X_OK):
        raise RuntimeError(f"二进制不可执行：{binary}")

    proc = subprocess.run(
        [str(binary), "--version"],
        capture_output=True,
        text=True,
        timeout=600,
        cwd=PROJECT_ROOT,
    )
    output = proc.stdout + proc.stderr
    if proc.returncode != 0 or "v1." not in output:
        raise RuntimeError(
            f"--version 探针失败（exit={proc.returncode}）：\n{output}"
        )
    print(f"[smoke] --version 探针通过：{output.strip().splitlines()[-1]}")

    # 随包文件抽查：资产目录与包内默认配置（协议库经探针 import 间接验证）
    internal = artifact_dir / "_internal"
    for required in (internal / "assets", internal / "config.yaml"):
        if not required.exists():
            raise RuntimeError(f"随包文件缺失：{required}")
    print(f"[smoke] 随包文件抽查通过：assets/、config.yaml（{artifact_dir.name}）")


def report_size(component: str, artifact_dir: Path) -> int:
    """产物体积统计（字节），打印并返回（冒烟记录用）。"""
    total = sum(f.stat().st_size for f in artifact_dir.rglob("*") if f.is_file())
    print(f"[smoke] {component} onedir 体积：{total / 1024 / 1024:.1f} MiB")
    return total


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--component",
        required=True,
        choices=[*COMPONENTS, "all"],
        help="构建目标组件",
    )
    parser.add_argument(
        "--appimage",
        action="store_true",
        help="onedir 构建后封装 AppImage（需 appimagetool 在 PATH）",
    )
    args = parser.parse_args()

    targets = list(COMPONENTS) if args.component == "all" else [args.component]
    for component in targets:
        print(f"[build] === {component} onedir ===", flush=True)
        artifact_dir = build_onedir(component)
        smoke_check(component, artifact_dir)
        report_size(component, artifact_dir)
        if args.appimage:
            # T4.3：AppDir 模板 + appimagetool 封装
            from appimage import build_appimage  # noqa: PLC0415

            print(f"[build] === {component} AppImage ===", flush=True)
            build_appimage(
                component, artifact_dir, DIST_DIR / APPIMAGE_NAMES[component]
            )
    print("[build] 全部完成", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
