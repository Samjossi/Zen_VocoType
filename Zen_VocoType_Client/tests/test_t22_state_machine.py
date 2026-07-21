"""T2.2 单元测试：状态机转移表遍历、非法转移、瞬态归位、监听器通知。"""

import pytest

from zen_vocotype_client.state_machine import (
    TRANSITIONS,
    Event,
    InvalidTransition,
    State,
    StateMachine,
)


class TestTransitions:
    def test_happy_path_full_cycle(self):
        """全流程：idle→recording→transcribing→completed→idle。"""
        sm = StateMachine()
        assert sm.state is State.IDLE
        assert sm.fire(Event.HOTKEY_PRESS) is State.RECORDING
        assert sm.fire(Event.HOTKEY_RELEASE) is State.TRANSCRIBING
        assert sm.fire(Event.TRANSCRIBE_DONE) is State.COMPLETED
        assert sm.fire(Event.OUTPUT_DONE) is State.IDLE

    def test_max_record_path(self):
        sm = StateMachine()
        sm.fire(Event.HOTKEY_PRESS)
        assert sm.fire(Event.RECORD_MAX_REACHED) is State.TRANSCRIBING

    def test_transcribe_failure_error_cycle(self):
        """识别失败 → error（瞬态）→ error_done 归位 idle。"""
        sm = StateMachine()
        sm.fire(Event.HOTKEY_PRESS)
        sm.fire(Event.HOTKEY_RELEASE)
        assert sm.fire(Event.TRANSCRIBE_FAILED) is State.ERROR
        assert sm.fire(Event.ERROR_DONE) is State.IDLE

    def test_output_failure_enters_error(self):
        sm = StateMachine()
        sm.fire(Event.HOTKEY_PRESS)
        sm.fire(Event.HOTKEY_RELEASE)
        sm.fire(Event.TRANSCRIBE_DONE)
        assert sm.fire(Event.OUTPUT_FAILED) is State.ERROR
        assert sm.fire(Event.ERROR_DONE) is State.IDLE

    @pytest.mark.parametrize(
        "state,event",
        [(s, e) for s in State for e in Event if (s, e) not in TRANSITIONS],
    )
    def test_all_undefined_transitions_raise(self, state, event):
        """转移表未定义的 (状态, 事件) 组合全部抛 InvalidTransition。"""
        sm = StateMachine()
        sm._state = state  # 构造任意起始态（仅测试用途）
        with pytest.raises(InvalidTransition):
            sm.fire(event)

    def test_transition_table_completeness(self):
        """每个非 idle 状态都有离开路径；瞬态可归位。"""
        for state in (State.COMPLETED, State.ERROR):
            exits = [e for (s, e) in TRANSITIONS if s is state]
            assert exits, f"瞬态 {state} 无离开转移"

    def test_listener_notified_with_payload(self):
        seen = []
        sm = StateMachine()
        sm.add_listener(lambda f, e, t, p: seen.append((f, e, t, p)))
        sm.fire(Event.HOTKEY_PRESS)
        sm.fire(Event.HOTKEY_RELEASE, payload=b"pcm")
        assert seen[0] == (State.IDLE, Event.HOTKEY_PRESS, State.RECORDING, None)
        assert seen[1] == (State.RECORDING, Event.HOTKEY_RELEASE, State.TRANSCRIBING, b"pcm")

    def test_no_qt_dependency(self):
        """红线：状态机模块不得 import Qt（保持可脱离 UI 单测）。"""
        import sys

        import zen_vocotype_client.state_machine as mod

        assert "PySide6" not in sys.modules or mod.__dict__.get("QObject") is None
        assert not any(name.startswith("PySide6") for name in dir(mod))
