#!/usr/bin/env python3
"""安装 Zen_VocoType 桌面入口（.desktop + hicolor 图标，幂等，纯用户态）。

用法：``.venv/bin/python tools/install_desktop.py [--dir <AppImage 摆放目录>]``

- ``--dir`` 缺省为脚本所在目录（分发形态：本脚本与三 AppImage 同目录摆放；
  仓库内开发用 ``--dir dist``）
- 重复执行结果一致（幂等）；卸载见 ``tools/uninstall_desktop.py``
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
    args = parser.parse_args()
    app_dir = args.dir.resolve()
    # 提取暂存落 AppImage 同目录下的临时子目录（用完即删；🔴 禁系统临时目录）
    written = install(app_dir, staging_root=app_dir / ".install-tmp")
    for path in written:
        print(f"[install] 已写入 {path}")
    print("[install] 桌面入口安装完成（幂等，可重复执行）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
