"""托盘「模型清单…」：注册表全量模型的只读详情对话框。

- 数据来源：进程内直读 ``Settings.models`` 注册表 + 当前模型名（ServiceState），
  🔴 不走 Socket 自连——托盘与服务同进程，绕协议徒增故障面
- 描述文本唯一出处：``ModelEntry.description``（注册表条目自带，
  用户自建条目同理可写；空描述显式标注「未提供描述」，不静默留白）
- ``model_id`` 条目附 ModelScope 官方页面链接（系统浏览器打开）；
  缓存状态按 modelscope 布局（``<models_dir>/models/<org>--<name>``）探测，
  未缓存提示「首次切换将自动下载」
"""

from __future__ import annotations

import html
from pathlib import Path

from ..config import ModelEntry, Settings

#: modelscope 缓存目录布局（MODELSCOPE_CACHE/models/<org>--<name>）
_CACHE_SUBDIR = "models"

#: ModelScope 官方页面地址模板（model_id 直接拼接）
_MODELSCOPE_PAGE = "https://www.modelscope.cn/models/{}"


def cache_status(entry: ModelEntry, models_dir: Path) -> str:
    """缓存状态展示文案（``local_path`` 直载条目无需缓存）。"""
    if entry.model_id is None:
        return "本地直载（无需下载）"
    cache_dir = (
        Path(models_dir) / _CACHE_SUBDIR / entry.model_id.replace("/", "--")
    )
    if cache_dir.is_dir():
        return "已缓存"
    return "未缓存（首次切换将自动下载）"


def build_models_html(settings: Settings, current_model: str | None) -> str:
    """构造模型清单 HTML（纯函数：对话框与单测共用的单一出处）。"""
    esc = html.escape
    header = (
        f"<p>共 <b>{len(settings.models)}</b> 个可切换模型；当前模型："
        f"<b>{esc(current_model) if current_model else '—（未加载）'}</b></p>"
    )
    sections = []
    for name in sorted(settings.models):
        entry = settings.models[name]
        is_current = name == current_model
        title = esc(name) + (' <span style="color:#3CA555">✅ 当前</span>' if is_current else "")
        loaded_text = "已加载（当前模型）" if is_current else "未加载"
        description = esc(entry.description) if entry.description else "（未提供描述）"
        rows = [
            ("状态", loaded_text),
            ("缓存", esc(cache_status(entry, settings.models_dir))),
            ("来源", esc(entry.source)),
            ("特点", description),
        ]
        if entry.model_id is not None:
            page = _MODELSCOPE_PAGE.format(entry.model_id)
            rows.append(("官网", f'<a href="{esc(page)}">ModelScope 页面</a>'))
        body = "".join(
            f'<tr><td style="padding:1px 12px 1px 0;vertical-align:top"><b>{k}</b></td>'
            f'<td style="vertical-align:top">{v}</td></tr>'
            for k, v in rows
        )
        sections.append(f"<h3 style=\"margin-bottom:2px\">{title}</h3><table>{body}</table>")
    return "<html><body>" + header + "<hr>" + "<hr>".join(sections) + "</body></html>"


def create_models_dialog(settings: Settings, current_model: str | None):
    """创建模型清单对话框（调用方负责 exec/show；测试直接持有检验）。"""
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QDialog, QDialogButtonBox, QTextBrowser, QVBoxLayout

    dialog = QDialog(None)
    dialog.setWindowTitle("模型清单")
    # 托盘无主窗口：置顶防对话框落到其他窗口之后
    dialog.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, True)
    dialog.resize(580, 440)
    browser = QTextBrowser(dialog)
    browser.setOpenExternalLinks(True)  # 官网链接走系统浏览器
    browser.setHtml(build_models_html(settings, current_model))
    buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
    buttons.rejected.connect(dialog.reject)
    layout = QVBoxLayout(dialog)
    layout.addWidget(browser)
    layout.addWidget(buttons)
    return dialog


def show_models_dialog(settings: Settings, current_model: str | None) -> None:
    """以模态方式弹出模型清单（嵌套事件循环，托盘轮询定时器不受影响）。"""
    create_models_dialog(settings, current_model).exec()


__all__ = [
    "build_models_html",
    "cache_status",
    "create_models_dialog",
    "show_models_dialog",
]
