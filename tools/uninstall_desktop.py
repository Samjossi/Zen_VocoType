#!/usr/bin/env python3
"""卸载 Zen_VocoType 桌面入口（.desktop + hicolor 图标，幂等）。

用法：``.venv/bin/python tools/uninstall_desktop.py``
"""

from __future__ import annotations

import sys

from desktop_entry import uninstall


def main() -> int:
    removed = uninstall()
    if removed:
        for path in removed:
            print(f"[uninstall] 已删除 {path}")
    else:
        print("[uninstall] 无已安装条目（幂等，无需操作）")
    print("[uninstall] 桌面入口卸载完成")
    return 0


if __name__ == "__main__":
    sys.exit(main())
