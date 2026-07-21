"""客户端状态机（选型五：手写枚举 + 集中转移表）。

状态划分（大纲 §4 继承）：

    idle → recording → transcribing → completed/error → idle

设计约束：

- 零依赖纯逻辑：不 import Qt，可脱离 UI 单测（选型五红线）；
  Qt 信号接线由装配层以监听器（``add_listener``）注入
- 非法转移抛 :class:`InvalidTransition`——非法转移即 bug，🔴 禁止静默吞掉
- ``completed``/``error`` 为瞬态：装配层在其中执行输出/提示动作后，
  必须立即以 ``output_done``/``output_failed``/``error_done`` 归位 ``idle``
- 状态机实例仅允许存在于主线程（选型一）；线程约束由装配层保证，本模块不自查
"""

from __future__ import annotations

import enum
from collections.abc import Callable


class State(enum.Enum):
    IDLE = "idle"
    RECORDING = "recording"
    TRANSCRIBING = "transcribing"
    COMPLETED = "completed"  # 瞬态：执行输出后立即归位
    ERROR = "error"  # 瞬态：执行提示后立即归位


class Event(enum.Enum):
    HOTKEY_PRESS = "hotkey_press"
    HOTKEY_RELEASE = "hotkey_release"
    RECORD_MAX_REACHED = "record_max_reached"
    TRANSCRIBE_DONE = "transcribe_done"
    TRANSCRIBE_FAILED = "transcribe_failed"
    OUTPUT_DONE = "output_done"
    OUTPUT_FAILED = "output_failed"
    ERROR_DONE = "error_done"  # 错误提示完成，瞬态归位


class InvalidTransition(Exception):
    """非法状态转移（等于发现了 bug，必须显式暴露）。"""


#: 集中转移表：(from_state, event) → to_state（单一出处，新增转移只改这里）
TRANSITIONS: dict[tuple[State, Event], State] = {
    (State.IDLE, Event.HOTKEY_PRESS): State.RECORDING,
    (State.RECORDING, Event.HOTKEY_RELEASE): State.TRANSCRIBING,
    (State.RECORDING, Event.RECORD_MAX_REACHED): State.TRANSCRIBING,
    (State.TRANSCRIBING, Event.TRANSCRIBE_DONE): State.COMPLETED,
    (State.TRANSCRIBING, Event.TRANSCRIBE_FAILED): State.ERROR,
    (State.COMPLETED, Event.OUTPUT_DONE): State.IDLE,
    (State.COMPLETED, Event.OUTPUT_FAILED): State.ERROR,
    (State.ERROR, Event.ERROR_DONE): State.IDLE,
}

#: 监听器签名：(from_state, event, to_state, payload)
TransitionListener = Callable[[State, Event, State, object], None]


class StateMachine:
    """状态机：集中转移表驱动，非法转移抛异常，转移通知监听器。"""

    def __init__(self) -> None:
        self._state: State = State.IDLE
        self._listeners: list[TransitionListener] = []

    @property
    def state(self) -> State:
        return self._state

    def add_listener(self, listener: TransitionListener) -> None:
        """注册转移监听器（装配层注入 Qt Signal.emit 或日志记录）。"""
        self._listeners.append(listener)

    def fire(self, event: Event, payload: object = None) -> State:
        """驱动一次转移。

        :param payload: 事件携带数据（如识别文本/错误信息），透传给监听器
        :raises InvalidTransition: 转移表未定义 (当前状态, event) 时
        """
        key = (self._state, event)
        if key not in TRANSITIONS:
            raise InvalidTransition(
                f"非法状态转移: {self._state.value} + {event.value}（转移表未定义）"
            )
        from_state = self._state
        to_state = TRANSITIONS[key]
        self._state = to_state
        for listener in self._listeners:
            listener(from_state, event, to_state, payload)
        return to_state
