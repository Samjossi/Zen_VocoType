"""T40 单元测试：双延迟配置字段 + 编排延迟/门控语义（fake deps 注入）。"""

from pathlib import Path

import pytest
from pydantic import ValidationError

from zen_vocotype_launcher.config import Settings
from zen_vocotype_launcher.discovery import (
    ComponentStatus,
    DiscoveryResult,
    SocketProbeResult,
)
from zen_vocotype_launcher.exit_codes import ExitCode
from zen_vocotype_launcher.orchestrator import (
    ComponentTarget,
    LaunchPlan,
    OrchestratorDeps,
    run,
)
from zen_vocotype_launcher.targets import build_plan


# ---------------------------------------------------------------------- 配置字段


class TestDelayFields:
    def test_defaults(self):
        s = Settings()
        assert s.service_start_delay_s == 0.0
        assert s.client_start_interval_s == 0.0
        assert s.auto_exit_delay_s == 8.0

    @pytest.mark.parametrize("value", [0, 1, 3.9])
    def test_auto_exit_below_min_rejected(self, value):
        """下限 4 秒（2026-07-23 实机事故：误设 0 → 幂等秒退「无托盘」错觉）。"""
        with pytest.raises(ValidationError):
            Settings(auto_exit_delay_s=value)

    def test_auto_exit_min_boundary_accepted(self):
        assert Settings(auto_exit_delay_s=4).auto_exit_delay_s == 4.0

    @pytest.mark.parametrize(
        "field",
        ["service_start_delay_s", "client_start_interval_s", "auto_exit_delay_s"],
    )
    def test_negative_rejected(self, field):
        with pytest.raises(ValidationError):
            Settings(**{field: -1})

    def test_delay_over_limit_rejected(self):
        with pytest.raises(ValidationError):
            Settings(service_start_delay_s=301)
        with pytest.raises(ValidationError):
            Settings(client_start_interval_s=301)

    def test_auto_exit_over_limit_rejected(self):
        with pytest.raises(ValidationError):
            Settings(auto_exit_delay_s=61)

    def test_launch_plan_delay_defaults_zero(self):
        plan = LaunchPlan(
            mode="prod",
            socket_path="/x.sock",
            service=ComponentTarget("service", ["/s"], "/s.lock", Path("s.log")),
            client=ComponentTarget("client", ["/c"], "/c.lock", Path("c.log")),
        )
        assert plan.service_delay_s == 0.0
        assert plan.client_interval_s == 0.0


# ---------------------------------------------------------------------- build_plan 双延迟携带


class TestBuildPlanDelays:
    def test_dev_plan_forces_zero_delays(self, monkeypatch):
        """dev 模式双延迟恒为 0，无视 Settings 值（开发编排不引入变量）。"""
        s = Settings(service_start_delay_s=7, client_start_interval_s=9)
        plan = build_plan(s, dev_mode=True)
        assert plan.mode == "dev"
        assert plan.service_delay_s == 0.0
        assert plan.client_interval_s == 0.0

    def test_prod_plan_carries_delays(self, monkeypatch, tmp_path):
        """正式模式 plan 携带 Settings 双延迟值。"""
        service_bin = tmp_path / "Zen_VocoType_Service.AppImage"
        client_bin = tmp_path / "Zen_VocoType_Client.AppImage"
        service_bin.touch()
        client_bin.touch()
        s = Settings(
            service_binary=str(service_bin),
            client_binary=str(client_bin),
            service_start_delay_s=3,
            client_start_interval_s=5,
        )
        plan = build_plan(s, dev_mode=False)
        assert plan.service_delay_s == 3.0
        assert plan.client_interval_s == 5.0


# ---------------------------------------------------------------------- 编排延迟/门控（fake deps）


class FakeProc:
    def __init__(self, name):
        self.name = name
        self.pid = 111 if name == "service" else 222
        self.terminated = False

    def is_alive(self):
        return True

    def poll(self):
        return None

    def terminate_group(self, grace):
        self.terminated = True


class FakeNotifier:
    def notify_starting(self):
        pass

    def notify_done(self, elapsed):
        pass

    def notify_failed(self, stage, log_hint):
        pass

    def notify_already_running(self, pid, mode):
        pass


class FakeLock:
    def __init__(self, path, mode):
        pass

    def acquire(self):
        pass

    def release(self):
        pass


def _plan(service_delay=0.0, client_interval=0.0) -> LaunchPlan:
    return LaunchPlan(
        mode="prod",
        socket_path="/x.sock",
        service=ComponentTarget("service", ["/s"], "/s.lock", Path("s.log")),
        client=ComponentTarget("client", ["/c"], "/c.lock", Path("c.log")),
        service_delay_s=service_delay,
        client_interval_s=client_interval,
    )


def _deps_with_events(events: list) -> OrchestratorDeps:
    """装配记录事件序的 fake deps（spawn/sleep/wait 全入列）。"""

    def spawn(argv, *, name, env=None, log_path=None):
        events.append(("spawn", name))
        return FakeProc(name)

    def discover(lock_path, *, name, expected_exe=None, expected_cmdline_fragment=None):
        return DiscoveryResult(ComponentStatus.ABSENT)

    def probe_socket(socket_path):
        return (SocketProbeResult.FREE, "")

    def wait_ready(client, **kwargs):
        events.append(("wait",))

    def fake_sleep(seconds):
        events.append(("sleep", seconds))

    return OrchestratorDeps(
        spawn=spawn,
        wait_for_readiness=wait_ready,
        discover=discover,
        probe_socket=probe_socket,
        client_factory=lambda path: object(),
        notifier=FakeNotifier(),
        lock_factory=FakeLock,
        sleep=fake_sleep,
    )


class TestOrchestrationDelays:
    def test_zero_delays_no_sleep(self):
        # T42：本文件聚焦倒计时语义，关闭存活确认窗口防 settle 轮询
        # sleep 污染事件流（存活确认由 test_t42_client_settle.py 覆盖）
        events: list = []
        code = run(
            _plan(),
            Settings(client_settle_timeout_s=0),
            "/x.lock",
            deps=_deps_with_events(events),
        )
        assert code == ExitCode.SUCCESS
        assert not [e for e in events if e[0] == "sleep"]

    def test_service_delay_before_service_spawn(self):
        events: list = []
        code = run(
            _plan(service_delay=3.0),
            Settings(client_settle_timeout_s=0),  # T42：关闭存活确认（见上）
            "/x.lock",
            deps=_deps_with_events(events),
        )
        assert code == ExitCode.SUCCESS
        sleeps = [e for e in events if e[0] == "sleep"]
        assert sum(s[1] for s in sleeps) == pytest.approx(3.0)
        first_sleep_idx = events.index(sleeps[0])
        service_spawn_idx = events.index(("spawn", "service"))
        assert first_sleep_idx < service_spawn_idx  # 延迟在拉起服务端之前

    def test_client_interval_between_spawns(self):
        events: list = []
        code = run(
            _plan(client_interval=5.0),
            Settings(client_settle_timeout_s=0),  # T42：关闭存活确认（见上）
            "/x.lock",
            deps=_deps_with_events(events),
        )
        assert code == ExitCode.SUCCESS
        sleeps = [e for e in events if e[0] == "sleep"]
        assert sum(s[1] for s in sleeps) == pytest.approx(5.0)
        first_sleep_idx = events.index(sleeps[0])
        last_sleep_idx = len(events) - 1 - events[::-1].index(sleeps[-1])
        assert events.index(("spawn", "service")) < first_sleep_idx
        assert last_sleep_idx < events.index(("spawn", "client"))

    def test_gating_wait_after_both_spawns(self):
        """T40 门控后移：就绪等待在两端拉起之后（整体成败判定）。"""
        events: list = []
        code = run(
            _plan(service_delay=2.0, client_interval=2.0),
            Settings(),
            "/x.lock",
            deps=_deps_with_events(events),
        )
        assert code == ExitCode.SUCCESS
        wait_idx = events.index(("wait",))
        assert events.index(("spawn", "service")) < wait_idx
        assert events.index(("spawn", "client")) < wait_idx

    def test_status_callback_countdown(self):
        """倒计时状态回调：进度文本含剩余秒数（🔴 不做无声等待）。"""
        events: list = []
        statuses: list[str] = []
        deps = _deps_with_events(events)
        deps.status_callback = statuses.append
        code = run(
            _plan(service_delay=3.0), Settings(), "/x.lock", deps=deps
        )
        assert code == ExitCode.SUCCESS
        countdown_texts = [t for t in statuses if "秒后启动服务端" in t]
        assert countdown_texts  # 至少有倒计时回调
        assert any("3 秒后启动服务端" in t for t in countdown_texts)

    def test_status_callback_none_cli_unaffected(self):
        """status_callback 默认 None：CLI 路径零影响（既有行为回归）。"""
        events: list = []
        deps = _deps_with_events(events)
        assert deps.status_callback is None
        code = run(_plan(), Settings(), "/x.lock", deps=deps)
        assert code == ExitCode.SUCCESS

    def test_delay_skipped_when_already_running(self):
        """既有实例幂等命中：不拉起也不等待（延迟仅作用于拉起分支）。"""
        events: list = []

        deps = _deps_with_events(events)

        def discover_running(lock_path, *, name, **kwargs):
            return DiscoveryResult(ComponentStatus.RUNNING, pid=999)

        deps.discover = discover_running
        code = run(
            _plan(service_delay=3.0, client_interval=3.0),
            Settings(),
            "/x.lock",
            deps=deps,
        )
        assert code == ExitCode.SUCCESS
        assert not [e for e in events if e[0] == "sleep"]
        assert not [e for e in events if e[0] == "spawn"]
