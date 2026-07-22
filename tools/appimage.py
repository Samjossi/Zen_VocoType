"""AppImage 封装（选型二方案 A）：手写 AppDir 模板 + appimagetool 纯封装。

设计要点：

- PyInstaller 已自收编 Python 依赖，AppImage 🔴 不做二次依赖收编——
  AppDir 仅是「onedir 拷入 usr/ + AppRun + .desktop + hicolor 图标」
- AppRun 经自身路径自定位 exec onedir 入口二进制（🔴 禁止 cwd 相对）
- appimagetool 查找顺序：``APPIMAGETOOL`` 环境变量 → PATH →
  ``tools/bin/appimagetool``（项目内副本）；全部缺席时明确报错并提示
  安装方式（🔴 禁止静默跳过封装）
"""

from __future__ import annotations

import os
import shutil
import stat
import subprocess
from pathlib import Path

#: 项目根（tools/appimage.py → 上一级）
PROJECT_ROOT: Path = Path(__file__).resolve().parents[1]

#: AppDir 暂存根（C8：构建临时产物落 ./.temp/）
APPDIR_STAGING: Path = PROJECT_ROOT / ".temp" / "build" / "appimage"

#: hicolor 图标尺寸族（与三组件资产四档一致；选型五桌面集成用）
ICON_SIZES: tuple[int, ...] = (32, 64, 128, 256)

#: 组件 → （onedir 产物名, AppImage 展示名, 图标源文件名模板——{} 为尺寸）
_APPIMAGE_META: dict[str, tuple[str, str, str]] = {
    "service": ("zen_vocotype_service", "Zen_VocoType Service", "zen_vocotype_service_icon_{}.png"),
    "client": ("zen_vocotype_client", "Zen_VocoType Client", "zen_vocotype_client_icon_{}.png"),
    "launcher": ("zen_vocotype_launcher", "Zen_VocoType Launcher", "zen_vocotype_launcher_icon_{}.png"),
}

#: AppRun 模板（_APPDIR 自定位，🔴 禁止 cwd 相对；不做任何环境 hack——
#: Qt 插件路径等由 PyInstaller 引导逻辑处理，见选型二）
_APPRUN_TEMPLATE = """#!/bin/sh
# {display_name} AppImage 入口（选型二方案 A：纯封装，不做二次依赖收编）
HERE="$(dirname "$(readlink -f "$0")")"
exec "$HERE/usr/{artifact}/{artifact}" "$@"
"""

#: AppDir 内嵌 .desktop 模板（供 AppImageLauncher 类工具集成；Icon= 与
#: 根目录图标文件同名，hicolor 同步摆放，见 AppImage 规范）
_DESKTOP_TEMPLATE = """[Desktop Entry]
Type=Application
Name={display_name}
Comment=Zen_VocoType voice typing component
Exec={artifact}
Icon={artifact}
Categories=Utility;
Terminal=false
"""


def _find_appimagetool() -> str:
    """定位 appimagetool；缺席时明确报错（🔴 禁止静默跳过封装）。"""
    candidates = [
        os.environ.get("APPIMAGETOOL"),
        shutil.which("appimagetool"),
        str(PROJECT_ROOT / "tools" / "bin" / "appimagetool"),
    ]
    for cand in candidates:
        if cand and Path(cand).is_file() and os.access(cand, os.X_OK):
            return cand
    raise RuntimeError(
        "appimagetool 未找到。安装方式（三选一）：\n"
        "  1. 设环境变量 APPIMAGETOOL=/路径/到/appimagetool\n"
        "  2. 将 appimagetool 放入 PATH\n"
        "  3. 放置项目内副本 tools/bin/appimagetool（x86_64 静态二进制，\n"
        "     官方发布：https://github.com/AppImage/appimagetool/releases）"
    )


def _stage_appdir(component: str, artifact_dir: Path) -> Path:
    """按模板搭建 AppDir（幂等：先清后建），返回 AppDir 路径。"""
    artifact, display_name, icon_pattern = _APPIMAGE_META[component]
    appdir = APPDIR_STAGING / f"{artifact}.AppDir"
    if appdir.exists():
        shutil.rmtree(appdir)

    # onedir 拷入 usr/（纯封装）
    payload = appdir / "usr" / artifact
    shutil.copytree(artifact_dir, payload)

    # AppRun（自定位 exec，可执行位）
    apprun = appdir / "AppRun"
    apprun.write_text(
        _APPRUN_TEMPLATE.format(display_name=display_name, artifact=artifact),
        encoding="utf-8",
    )
    apprun.chmod(apprun.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)

    # .desktop（AppDir 根，Icon= 与图标文件同名）
    (appdir / f"{artifact}.desktop").write_text(
        _DESKTOP_TEMPLATE.format(display_name=display_name, artifact=artifact),
        encoding="utf-8",
    )

    # 图标：根目录（256px，AppImage 规范要求）+ hicolor 四档规范位
    # （四档供 tools/install_desktop.py 提取安装至用户 hicolor 主题）
    icon_root_src = artifact_dir / "_internal" / "assets" / icon_pattern.format(256)
    if not icon_root_src.is_file():
        raise RuntimeError(f"AppImage 图标源缺失：{icon_root_src}")
    shutil.copy2(icon_root_src, appdir / f"{artifact}.png")
    for size in ICON_SIZES:
        src = artifact_dir / "_internal" / "assets" / icon_pattern.format(size)
        if not src.is_file():
            raise RuntimeError(f"AppImage 图标源缺失：{src}")
        hicolor = appdir / "usr" / "share" / "icons" / "hicolor" / f"{size}x{size}" / "apps"
        hicolor.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, hicolor / f"{artifact}.png")
    return appdir


def build_appimage(component: str, artifact_dir: Path, output_path: Path) -> Path:
    """封装单组件 AppImage，返回产物路径。

    :param component: 组件键（service/client/launcher）
    :param artifact_dir: onedir 产物目录（须已过冒烟）
    :param output_path: AppImage 输出路径（dist/ 内）
    """
    tool = _find_appimagetool()
    appdir = _stage_appdir(component, artifact_dir)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.unlink(missing_ok=True)

    env = os.environ.copy()
    env["ARCH"] = "x86_64"  # appimagetool 老版本不自动探测宿主架构
    print(f"[appimage] $ {tool} {appdir} {output_path}", flush=True)
    subprocess.run(
        [tool, str(appdir), str(output_path)],
        check=True,
        env=env,
        cwd=PROJECT_ROOT,
    )
    if not output_path.is_file():
        raise RuntimeError(f"AppImage 产物缺失：{output_path}")
    size_mib = output_path.stat().st_size / 1024 / 1024
    print(f"[appimage] {component} 封装完成：{size_mib:.1f} MiB（{output_path.name}）")
    return output_path


__all__ = ["build_appimage"]
