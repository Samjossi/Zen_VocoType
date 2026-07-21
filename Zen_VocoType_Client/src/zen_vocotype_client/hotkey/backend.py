"""热键后端抽象（选型三：为 Wayland 迁移预留 evdev/Portal 后端）。

后端职责仅限「按下/松开两个事件的可靠产出」；组合键解析与去抖逻辑在
``combo.py`` 与本模块的 ``ComboTracker``（纯逻辑，可合成事件单测）。

🔴 红线（选型一）：后端实现的事件回调在其原生监听线程内触发，
装配层必须注册 Qt Signal.emit（线程安全），禁止直接触碰业务状态。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Callable

from pynput import keyboard

from .combo import HotkeyCombo, modifier_name


def _canonical_key(key) -> tuple:
    """按键规范化为可比较元组。

    两种来源的按键对象表示不一致（实测定案，2026-07-21）：

    - ``HotKey.parse("<f9>")`` 产出 ``KeyCode(vk=65478)``，而 Listener 事件
      传来 ``Key.f9``（枚举，``.value`` 为同 vk 的 KeyCode）——特殊键须按 vk 比较
    - 字符键恰好相反：``HotKey.parse("o")`` 产出 ``KeyCode(char='o', vk=None)``，
      而物理按键事件传来 ``KeyCode(vk=32, char='o')``（带物理键码）——
      字符键须按 char 比较（忽略 vk），否则物理按键永不匹配

    规则：char 非空按 char 匹配；char 为空（特殊键）按 vk 匹配。
    """
    if isinstance(key, keyboard.Key):
        key = key.value  # Key 枚举 → 其 KeyCode 值
    char = getattr(key, "char", None)
    if char is not None:
        return ("char", char)
    return ("vk", getattr(key, "vk", None))


class ComboTracker:
    """组合键状态机：修饰键集合跟踪 + 主键按住/松开语义 + 键重复去抖。

    语义（选型三定稿）：

    - 全部所需修饰键按下中，主键**首次**按下 → 产出一个 press 事件
    - 激活期间主键的系统键重复 → 忽略（🔴 禁止重复 press）
    - 激活期间**主键释放** → 产出一个 release 事件（以主键释放为准，
      提前松开修饰键不结束录音）
    """

    def __init__(self, combo: HotkeyCombo) -> None:
        self._combo = combo
        self._pressed_modifiers: set[str] = set()
        self._active = False  # 热键激活中（已产出 press、未产出 release）

    @property
    def active(self) -> bool:
        return self._active

    def _matches_main_key(self, key) -> bool:
        return _canonical_key(key) == _canonical_key(self._combo.key)

    def press(self, key) -> bool:
        """按键按下事件注入；返回 True 表示应产出热键 press。"""
        name = modifier_name(key)
        if name is not None:
            self._pressed_modifiers.add(name)
            return False
        if self._matches_main_key(key):
            if self._active:
                return False  # 系统键重复去抖
            if self._combo.modifiers <= self._pressed_modifiers:
                self._active = True
                return True
        return False

    def release(self, key) -> bool:
        """按键松开事件注入；返回 True 表示应产出热键 release。"""
        name = modifier_name(key)
        if name is not None:
            self._pressed_modifiers.discard(name)
            return False
        if self._matches_main_key(key) and self._active:
            self._active = False
            return True
        return False


class HotkeyBackend(ABC):
    """热键后端抽象：start/stop 生命周期 + 事件回调注入。"""

    def __init__(
        self,
        combo: HotkeyCombo,
        on_press: Callable[[], None],
        on_release: Callable[[], None],
    ) -> None:
        self._combo = combo
        self._on_press = on_press
        self._on_release = on_release

    @abstractmethod
    def start(self) -> None:
        """启动监听（失败必须抛明确异常，🔴 禁止静默降级为无热键空跑）。"""

    @abstractmethod
    def stop(self) -> None:
        """停止监听并释放资源（应用退出序列）。"""
