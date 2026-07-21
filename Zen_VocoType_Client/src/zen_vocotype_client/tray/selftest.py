"""托盘布局截图自检（开发自查工具：查看→验证→修改→再验证循环）。

命令行用法（客户端 ``main.py --screenshot <输出目录>`` 进入）：

1. 启动真实托盘（当前桌面会话），延时待图标稳定显示
2. 全屏截图（托盘图标区域肉眼可查）
3. 自动展开托盘右键菜单并抓菜单窗口特写（版本项/菜单布局验证）
4. 离屏渲染全部状态色图标合成图（状态色验证，不依赖托盘）
5. 自检结果文本落盘后退出

🔴 本模式仅用于开发自查，不改变正常模式任何行为。
"""

from __future__ import annotations

import sys
from pathlib import Path

from loguru import logger

#: 各阶段延时（毫秒）。依据：托盘图标注册与菜单动画在当前桌面实测 < 1s，
#: 取 1.5s/0.6s 保守值；仅开发自查工具使用，非业务同步手段
_SETTLE_DELAY_MS = 1500
_MENU_DELAY_MS = 600


def run_screenshot_mode(output_dir: Path) -> int:
    """执行截图自检，返回退出码（0=成功生成全部截图）。"""
    from PySide6.QtCore import QTimer
    from PySide6.QtGui import QGuiApplication, QPainter, QPixmap
    from PySide6.QtWidgets import QApplication

    from .icon_loader import load_tray_icon
    from .tray import ClientTray, TrayStatus, status_icon

    output_dir.mkdir(parents=True, exist_ok=True)
    app = QApplication.instance() or QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)

    tray = ClientTray()
    tray.show()

    steps: list[str] = []

    def _grab_fullscreen(name: str) -> None:
        pix = QGuiApplication.primaryScreen().grabWindow(0)
        path = output_dir / name
        pix.save(str(path))
        steps.append(f"{name}: {pix.width()}x{pix.height()}")

    def step_fullscreen() -> None:
        _grab_fullscreen("01_全屏_托盘图标.png")
        # 展开右键菜单（定位托盘图标几何中心；取不到则退屏幕右上角附近）
        geo = tray.tray_icon.geometry()
        menu = tray.tray_icon.contextMenu()
        pos = geo.center() if geo.isValid() else app.primaryScreen().geometry().topRight()
        menu.popup(pos)
        steps.append(f"菜单弹出位置: ({pos.x()}, {pos.y()})，托盘几何有效: {geo.isValid()}")
        QTimer.singleShot(_MENU_DELAY_MS, step_menu)

    def step_menu() -> None:
        menu = tray.tray_icon.contextMenu()
        pix = QGuiApplication.primaryScreen().grabWindow(menu.winId())
        path = output_dir / "02_托盘右键菜单.png"
        pix.save(str(path))
        steps.append(f"02_托盘右键菜单.png: {pix.width()}x{pix.height()}")
        menu.close()
        _grab_fullscreen("03_全屏_菜单展开.png")
        QTimer.singleShot(0, step_status_icons)

    def step_status_icons() -> None:
        # 全状态色合成图（离屏渲染，与桌面环境无关）
        base = load_tray_icon()
        size, pad = 64, 8
        pix = QPixmap((size + pad) * len(TrayStatus) + pad, size + pad * 2)
        pix.fill()
        painter = QPainter(pix)
        for i, status in enumerate(TrayStatus):
            icon = status_icon(base, status, size)
            icon.paint(painter, pad + i * (size + pad), pad, size, size)
        painter.end()
        path = output_dir / "04_状态色图标全览.png"
        pix.save(str(path))
        steps.append(f"04_状态色图标全览.png: {len(TrayStatus)} 状态，顺序："
                     + "/".join(s.name for s in TrayStatus))
        finish(0)

    def finish(code: int) -> None:
        (output_dir / "自检结果.txt").write_text("\n".join(steps) + "\n", encoding="utf-8")
        logger.info("托盘截图自检完成，产物目录：{}", output_dir)
        app.exit(code)

    QTimer.singleShot(_SETTLE_DELAY_MS, step_fullscreen)
    return app.exec()
