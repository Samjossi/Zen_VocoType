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


#: 修饰键展示顺序与显示名（固定序，与配置书写习惯一致）
_MODIFIER_DISPLAY_ORDER: tuple[str, ...] = ("ctrl", "alt", "shift", "cmd")
_MODIFIER_DISPLAY_NAMES: dict[str, str] = {
    "ctrl": "Ctrl",
    "alt": "Alt",
    "shift": "Shift",
    "cmd": "Cmd",
}


#: vk → 特殊键名反查表（HotKey.parse("<f9>") 产出 KeyCode(vk=…)，非 Key 枚举，
#: 与 backend._canonical_key 实测结论一致——特殊键须按 vk 归位再取名）
_VK_TO_KEY_NAME: dict[int, str] = {
    member.value.vk: member.name
    for member in keyboard.Key
    if getattr(member.value, "vk", None) is not None
}


def _format_key_name(name: str) -> str:
    """pynput 键名 → 展示写法（f1…f12 全大写；其余按下划线分段首字母大写，
    page_up → Page_Up——str.capitalize 会小写其余字符，不可整串调用）。"""
    if name.startswith("f") and name[1:].isdigit():
        return name.upper()
    return "_".join(part.capitalize() for part in name.split("_"))


def _key_display_name(key: keyboard.Key | keyboard.KeyCode) -> str:
    """主键 → 人类可读写法（字符大写；特殊键经 vk 反查键名）。"""
    if isinstance(key, keyboard.Key):
        return _format_key_name(key.name)
    char = getattr(key, "char", None)
    if char is not None:
        return char.upper()
    vk = getattr(key, "vk", None)
    if vk is not None and vk in _VK_TO_KEY_NAME:
        return _format_key_name(_VK_TO_KEY_NAME[vk])
    return f"VK{vk}" if vk is not None else "?"


def format_hotkey_display(expression: str) -> str:
    """pynput 组合键表达式 → 人类可读展示文本（``<ctrl>+<alt>+o`` → ``Ctrl+Alt+O``）。

    供托盘菜单展示行与捕获对话框回显共用（表达式知识单一出处在本模块，
    🔴 禁止 UI 层另写解析）。非法表达式原样回显，不抛异常。
    """
    try:
        combo = parse_hotkey(expression)
    except ValueError:
        return expression
    parts = [
        _MODIFIER_DISPLAY_NAMES[name]
        for name in _MODIFIER_DISPLAY_ORDER
        if name in combo.modifiers
    ]
    parts.append(_key_display_name(combo.key))
    return "+".join(parts)
