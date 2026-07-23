#!/usr/bin/env python3
"""安装 Zen_VocoType 桌面入口（.desktop + hicolor 图标，幂等，纯用户态）。

用法：``.venv/bin/python tools/install_desktop.py [--dir <AppImage 摆放目录>] [--autostart]``

- ``--dir`` 缺省为脚本所在目录（分发形态：本脚本与三 AppImage 同目录摆放；
  仓库内开发用 ``--dir dist``）
- ``--autostart``（T43）：同时安装 GNOME 开机自启动条目
  （``~/.config/autostart/zen-vocotype.desktop``，图形会话就绪后执行，
  DISPLAY 天然齐备；🔴 请勿改用 systemd 用户服务——2026-07-23 实机事故，
  见 work plans 诊断报告）。若曾手工配置 systemd 自启动服务，请先
  ``systemctl --user disable --now <单元>`` 再装本条目（防双启动）
- 重复执行结果一致（幂等）；卸载见 ``tools/uninstall_desktop.py``
  （autostart 条目一并删除，与安装期是否启用无关）
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from desktop_entry import install


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dir",
        type=Path,
        default=Path(__file__).resolve().parent,
        help="三 AppImage 摆放目录（默认：本脚本所在目录）",
    )
    parser.add_argument(
        "--autostart",
        action="store_true",
        help="同时安装 GNOME 开机自启动条目（~/.config/autostart/）",
    )
    args = parser.parse_args()
    app_dir = args.dir.resolve()
    # 提取暂存落 AppImage 同目录下的临时子目录（用完即删；🔴 禁系统临时目录）
    written = install(app_dir, staging_root=app_dir / ".install-tmp", autostart=args.autostart)
    for path in written:
        print(f"[install] 已写入 {path}")
    print("[install] 桌面入口安装完成（幂等，可重复执行）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
