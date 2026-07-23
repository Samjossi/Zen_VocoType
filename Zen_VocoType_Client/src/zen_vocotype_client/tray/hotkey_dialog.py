"""热键捕获对话框（托盘「修改快捷键…」入口，T3.3）。

两部分：

- :func:`qt_event_to_expression`：Qt 按键事件 → pynput 组合键表达式的
  **白名单映射**纯函数（无显示环境可单测）；未收录按键明确返回 None，
  🔴 禁止猜测映射落盘非法表达式
- :class:`HotkeyCaptureDialog`：模态捕获对话框，实时回显组合键，
  「确定」前经 ``hotkey.combo.parse_hotkey`` 预校验（解析逻辑单一出处）
"""

from __future__ import annotations

from loguru import logger
from PySide6.QtCore import QEvent, Qt
from PySide6.QtGui import QKeyEvent
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ..hotkey.combo import format_hotkey_display, parse_hotkey

#: Qt 修饰键 → pynput 修饰键段（固定四项；拼接顺序见 _MODIFIER_ORDER）
_QT_MODIFIERS: tuple[tuple[Qt.KeyboardModifier, str], ...] = (
    (Qt.KeyboardModifier.ControlModifier, "<ctrl>"),
    (Qt.KeyboardModifier.AltModifier, "<alt>"),
    (Qt.KeyboardModifier.ShiftModifier, "<shift>"),
    (Qt.KeyboardModifier.MetaModifier, "<cmd>"),
)

#: Qt 修饰键伪主键（按下修饰键本身时 event.key() 的值，不作为主键）
_QT_MODIFIER_KEYS: frozenset[int] = frozenset(
    int(k)
    for k in (
        Qt.Key.Key_Control,
        Qt.Key.Key_Alt,
        Qt.Key.Key_Shift,
        Qt.Key.Key_Meta,
        Qt.Key.Key_AltGr,
    )
)


def _build_main_key_map() -> dict[int, str]:
    """主键白名单映射表：Qt.Key（int 值）→ pynput 键段。"""
    mapping: dict[int, str] = {}
    # 字母（小写字符，与配置书写习惯一致）
    for code in range(int(Qt.Key.Key_A), int(Qt.Key.Key_Z) + 1):
        mapping[code] = chr(code).lower()
    # 数字
    for code in range(int(Qt.Key.Key_0), int(Qt.Key.Key_9) + 1):
        mapping[code] = chr(code)
    # 功能键 F1…F12
    for i in range(1, 13):
        mapping[int(getattr(Qt.Key, f"Key_F{i}"))] = f"<f{i}>"
    # 常用特殊键（pynput 同名 <> 键）
    mapping.update(
        {
            int(Qt.Key.Key_Space): "<space>",
            int(Qt.Key.Key_Tab): "<tab>",
            # Shift+Tab 在 Qt 中上报为 Key_Backtab（Shift 已在 modifiers 中）
            int(Qt.Key.Key_Backtab): "<tab>",
            int(Qt.Key.Key_Backspace): "<backspace>",
            int(Qt.Key.Key_Up): "<up>",
            int(Qt.Key.Key_Down): "<down>",
            int(Qt.Key.Key_Left): "<left>",
            int(Qt.Key.Key_Right): "<right>",
            int(Qt.Key.Key_Insert): "<insert>",
            int(Qt.Key.Key_Delete): "<delete>",
            int(Qt.Key.Key_Home): "<home>",
            int(Qt.Key.Key_End): "<end>",
            int(Qt.Key.Key_PageUp): "<page_up>",
            int(Qt.Key.Key_PageDown): "<page_down>",
        }
    )
    # 主键区标点（含 Shift 移位变体：Qt 按移位后键符上报，
    # 如 Shift+` → Key_AsciiTilde；pynput 解析与 char 匹配已实证，T36）
    mapping.update(
        {
            int(Qt.Key.Key_QuoteLeft): "`",
            int(Qt.Key.Key_AsciiTilde): "~",
            int(Qt.Key.Key_Minus): "-",
            int(Qt.Key.Key_Underscore): "_",
            int(Qt.Key.Key_Equal): "=",
            int(Qt.Key.Key_Plus): "+",
            int(Qt.Key.Key_BracketLeft): "[",
            int(Qt.Key.Key_BracketRight): "]",
            int(Qt.Key.Key_BraceLeft): "{",
            int(Qt.Key.Key_BraceRight): "}",
            int(Qt.Key.Key_Backslash): "\\",
            int(Qt.Key.Key_Bar): "|",
            int(Qt.Key.Key_Semicolon): ";",
            int(Qt.Key.Key_Colon): ":",
            int(Qt.Key.Key_Apostrophe): "'",
            int(Qt.Key.Key_QuoteDbl): '"',
            int(Qt.Key.Key_Comma): ",",
            int(Qt.Key.Key_Less): "<",
            int(Qt.Key.Key_Period): ".",
            int(Qt.Key.Key_Greater): ">",
            int(Qt.Key.Key_Slash): "/",
            int(Qt.Key.Key_Question): "?",
        }
    )
    return mapping


#: 主键白名单（未收录 → 不支持，对话框内提示）
_QT_TO_PYNPUT: dict[int, str] = _build_main_key_map()


def qt_event_to_expression(
    key: int, modifiers: Qt.KeyboardModifiers
) -> str | None:
    """QKeyEvent 的 key/modifiers → pynput 组合键表达式。

    :return: 形如 ``<ctrl>+<alt>+o`` 的表达式；主键不在白名单或按下的是
        纯修饰键时返回 None
    """
    if key in _QT_MODIFIER_KEYS:
        return None
    main = _QT_TO_PYNPUT.get(key)
    if main is None:
        return None
    parts = [segment for flag, segment in _QT_MODIFIERS if modifiers & flag]
    parts.append(main)
    return "+".join(parts)


class HotkeyCaptureDialog(QDialog):
    """热键捕获对话框：按下目标组合键 → 实时回显 → 确定生效。

    公开属性 :attr:`expression`：accept 后由调用方读取（None = 未捕获到
    有效组合）。「恢复默认」按钮以 ``Settings.hotkey`` 字段默认值直接
    accept（🔴 禁止硬编码第二份默认值）。
    """

    def __init__(self, current: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("修改快捷键")
        self.setModal(True)
        self.setMinimumWidth(360)

        #: 捕获到并校验通过的 pynput 表达式（accept 后由调用方读取）
        self.expression: str | None = None

        layout = QVBoxLayout(self)

        self._hint_label = QLabel(
            f"当前快捷键：{format_hotkey_display(current)}\n"
            "请直接按下新的快捷键组合（Esc 取消）"
        )
        self._hint_label.setWordWrap(True)
        layout.addWidget(self._hint_label)

        #: 大号回显框（捕获组合的人类可读文本 / 错误提示）
        self._display_label = QLabel("（等待按键…）")
        font = self._display_label.font()
        font.setPointSize(font.pointSize() + 4)
        font.setBold(True)
        self._display_label.setFont(font)
        self._display_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self._display_label)

        self._buttons = QDialogButtonBox()
        self._ok_button: QPushButton = self._buttons.addButton(
            "确定", QDialogButtonBox.ButtonRole.AcceptRole
        )
        self._ok_button.setEnabled(False)  # 捕获到有效组合前禁用
        self._cancel_button: QPushButton = self._buttons.addButton(
            "取消", QDialogButtonBox.ButtonRole.RejectRole
        )
        self._default_button: QPushButton = self._buttons.addButton(
            "恢复默认", QDialogButtonBox.ButtonRole.ResetRole
        )
        self._buttons.accepted.connect(self._on_accept)
        self._buttons.rejected.connect(self.reject)
        self._default_button.clicked.connect(self._on_restore_default)
        layout.addWidget(self._buttons)

        self._candidate: str | None = None  # 已捕获、待确认的表达式

    # ------------------------------------------------------------------ 捕获

    def event(self, event) -> bool:  # noqa: N802（Qt 命名）
        """Tab/Backtab 拦截：Qt 焦点导航在 keyPressEvent 之前消费 Tab，
        不重写 event() 则 ``<tab>`` 组合永远捕获不到（T3.3 评审修复）。"""
        if (
            event.type() == QEvent.Type.KeyPress
            and event.key() in (int(Qt.Key.Key_Tab), int(Qt.Key.Key_Backtab))
        ):
            self.keyPressEvent(event)
            return True
        return super().event(event)

    def keyPressEvent(self, event: QKeyEvent) -> None:  # noqa: N802（Qt 命名）
        key = event.key()
        if key == int(Qt.Key.Key_Escape):
            self.reject()
            return
        if key in _QT_MODIFIER_KEYS:
            # 纯修饰键：回显「已按下修饰键 + …」等待主键
            parts = [
                segment
                for flag, segment in _QT_MODIFIERS
                if event.modifiers() & flag
            ]
            self._display_label.setText("+".join(parts) + "+…" if parts else "（等待按键…）")
            return
        expression = qt_event_to_expression(key, event.modifiers())
        if expression is None:
            self._candidate = None
            self._ok_button.setEnabled(False)
            self._display_label.setText(f"不支持的按键：{event.text() or key}")
            return
        self._candidate = expression
        self._ok_button.setEnabled(True)
        self._display_label.setText(format_hotkey_display(expression))

    # ------------------------------------------------------------------ 确认

    def _on_accept(self) -> None:
        """「确定」：parse_hotkey 防御性兜底校验，非法则回显错误不放行。"""
        if self._candidate is None:
            return
        try:
            parse_hotkey(self._candidate)
        except ValueError as exc:
            logger.warning("捕获对话框校验拦截非法表达式：{}", exc)
            self._display_label.setText(f"非法快捷键：{exc}")
            self._candidate = None
            self._ok_button.setEnabled(False)
            return
        self.expression = self._candidate
        self.accept()

    def _on_restore_default(self) -> None:
        """「恢复默认」：以 Settings.hotkey 字段默认值直接 accept。"""
        from ..config import Settings  # 延迟 import：对话框不反向依赖装配层

        self.expression = Settings.model_fields["hotkey"].default
        self.accept()
