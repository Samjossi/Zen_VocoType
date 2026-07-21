"""热键组合键表达式解析（纯逻辑，不依赖 X11/显示服务器）。

pynput 组合键表达式（如 ``<ctrl>+```）解析为「修饰键集合 + 主键」结构，
供启动期配置校验（config.validate_startup）与运行期组合键状态机
（T2.5 PynputBackend）共用——🔴 解析逻辑单一出处，禁止两处漂移。
"""

from __future__ import annotations

from dataclasses import dataclass

from pynput import keyboard


@dataclass(frozen=True)
class HotkeyCombo:
    """解析后的组合键：修饰键规范名集合 + 主键。"""

    #: 修饰键规范名集合（如 {"ctrl"}；pynput <ctrl_l>/<ctrl_r> 归一为 ctrl）
    modifiers: frozenset[str]

    #: 主键（pynput 按键对象：Key 枚举或 KeyCode）
    key: keyboard.Key | keyboard.KeyCode

    #: 原始表达式（日志与报错回显用）
    expression: str


#: pynput 修饰键枚举 → 规范名（左右变体归一）
_MODIFIER_NAMES: dict[keyboard.Key, str] = {
    keyboard.Key.ctrl: "ctrl",
    keyboard.Key.ctrl_l: "ctrl",
    keyboard.Key.ctrl_r: "ctrl",
    keyboard.Key.alt: "alt",
    keyboard.Key.alt_l: "alt",
    keyboard.Key.alt_r: "alt",
    keyboard.Key.alt_gr: "alt",
    keyboard.Key.shift: "shift",
    keyboard.Key.shift_l: "shift",
    keyboard.Key.shift_r: "shift",
    keyboard.Key.cmd: "cmd",
    keyboard.Key.cmd_l: "cmd",
    keyboard.Key.cmd_r: "cmd",
}


def modifier_name(key: keyboard.Key | keyboard.KeyCode) -> str | None:
    """返回按键的修饰键规范名；非修饰键返回 None。"""
    return _MODIFIER_NAMES.get(key)


def parse_hotkey(expression: str) -> HotkeyCombo:
    """解析 pynput 组合键表达式。

    :raises ValueError: 表达式为空、无法解析、无主键或主键本身是修饰键
    """
    if not expression or not expression.strip():
        raise ValueError("热键表达式为空")
    try:
        keys = keyboard.HotKey.parse(expression.strip())
    except Exception as exc:  # pynput 对非法键名抛 ValueError/KeyError，统一收敛
        raise ValueError(f"热键表达式无法解析: {expression!r}（{exc}）") from exc
    if not keys:
        raise ValueError(f"热键表达式无有效按键: {expression!r}")

    modifiers: set[str] = set()
    normal_keys: list[keyboard.Key | keyboard.KeyCode] = []
    for key in keys:
        name = modifier_name(key)
        if name is not None:
            modifiers.add(name)
        else:
            normal_keys.append(key)

    if len(normal_keys) != 1:
        raise ValueError(
            f"热键表达式须恰好含一个非修饰主键: {expression!r}（解析出 {len(normal_keys)} 个）"
        )
    return HotkeyCombo(
        modifiers=frozenset(modifiers),
        key=normal_keys[0],
        expression=expression.strip(),
    )
