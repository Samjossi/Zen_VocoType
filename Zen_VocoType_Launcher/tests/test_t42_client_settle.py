"""T42 编排器 Client 存活确认窗口单测（2026-07-23 systemd 实机事故）。

事故背景：AppImage FUSE 引导 + Python 启动需数秒，拉起瞬间 ``is_alive()``
必然为真；Client 引导完成后 Qt 硬崩（无显示环境）落在既有检查盲区，
「启动完成」误报。修复：就绪等待成功后、``notify_done`` 前对本进程拉起的
Client 做轮次制存活确认（``client_settle_timeout_s`` 窗口，deps.sleep
注入，测试零真实等待）；🔴 幂等命中路径跳过确认（零附加等待红线）。
"""

import math
from pathlib import Path

from zen_vocotype_launcher.config import Settings
from zen_vocotype_launcher.discovery import (
    ComponentStatus,
    DiscoveryResult,
    SocketProbeResult,
)
from zen_vocotype_launcher.exit_codes import ExitCode
from zen_vocotype_launcher.orchestrator import (
    _SETTLE_POLL_S,
    ComponentTarget,
    LaunchPlan,
    OrchestratorDeps,
    run,
)


# ---------------------------------------------------------------------- fakes


class FakeProc:
    """受管子进程 fake：可预设「第 N 次 is_alive 起死亡」（引导后崩溃模拟）。"""

    def __init__(self, name, die_after_checks: int | None = None):
        self.name = name
        self.pid = 111 if name == "service" else 222
        self.terminated = False
        self._checks = 0
        self._die_after = die_after_checks  # None = 全程存活

    def is_alive(self):
        self._checks += 1
        if self._die_after is not None and self._checks > self._die_after:
            return False
        return True

    def poll(self):
        return None if self.is_alive() else 1

    def terminate_group(self, grace):
        self.terminated = True


class FakeNotifier:
    def __init__(self):
        self.events: list[tuple] = []

    def notify_starting(self):
        self.events.append(("starting",))

    def notify_done(self, elapsed):
        self.events.append(("done", elapsed))

    def notify_failed(self, stage, log_hint):
        self.events.append(("failed", stage))

    def notify_already_running(self, pid, mode):
        self.events.append(("already", pid, mode))


class FakeLock:
    def __init__(self, path, mode):
        pass

    def acquire(self):
        pass

    def release(self):
        pass


class FakeDeps:
    """按场景装配 OrchestratorDeps（sleep 注入为计数 fake，零真实等待）。"""

    def __init__(self):
        self.spawned: list[FakeProc] = []
        self.notifier = FakeNotifier()
        self.sleeps: list[float] = []
        self.discover_results: dict[str, DiscoveryResult] = {}
        self.client_die_after: int | None = None
        self.kill_client_in_wait = False

    def build(self) -> OrchestratorDeps:
        outer = self

        def spawn(argv, *, name, env=None, log_path=None):
            die = outer.client_die_after if name == "client" else None
            proc = FakeProc(name, die_after_checks=die)
            outer.spawned.append(proc)
            return proc

        def discover(lock_path, *, name, expected_exe=None, expected_cmdline_fragment=None):
            return outer.discover_results.get(
                name, DiscoveryResult(ComponentStatus.ABSENT)
            )

        def probe_socket(socket_path):
            return (SocketProbeResult.FREE, "")

        def wait_ready(client, **kwargs):
            # 场景：Client 在就绪等待期间死亡（窗口在等待期耗尽，首轮终检捕获）
            if outer.kill_client_in_wait:
                for p in outer.spawned:
                    if p.name == "client":
                        p._die_after = 0

        def fake_sleep(seconds):
            outer.sleeps.append(seconds)

        return OrchestratorDeps(
            spawn=spawn,
            wait_for_readiness=wait_ready,
            discover=discover,
            probe_socket=probe_socket,
            client_factory=lambda path: object(),
            notifier=outer.notifier,
            lock_factory=FakeLock,
            log_file=Path("launcher.log"),
            sleep=fake_sleep,
        )


def _plan() -> LaunchPlan:
    return LaunchPlan(
        mode="prod",
        socket_path="/x.sock",
        service=ComponentTarget("service", ["/s"], "/s.lock", Path("s.log")),
        client=ComponentTarget("client", ["/c"], "/c.lock", Path("c.log")),
    )


def _settings(**overrides) -> Settings:
    return Settings(**overrides)


# ---------------------------------------------------------------------- 用例


class TestSettleDeath:
    def test_client_dies_within_window_fails(self):
        """存活确认期内死亡 → CLIENT_FAILED + 通知 + 回收 owned Service，
        🔴 禁止 notify_done 误报（本任务核心场景）。"""
        deps = FakeDeps()
        deps.client_die_after = 3  # 拉起瞬间检查 1 次 + 确认期第 3 次起死亡
        code = run(_plan(), _settings(), "/x.lock", deps=deps.build())
        assert code == ExitCode.CLIENT_FAILED
        kinds = [e[0] for e in deps.notifier.events]
        assert "failed" in kinds
        assert "done" not in kinds  # 误报窗口已堵
        service = next(p for p in deps.spawned if p.name == "service")
        assert service.terminated  # ExitStack 逆序回收本进程拉起的 Service

    def test_client_dies_during_readiness_wait(self):
        """Client 在就绪等待期间死亡（窗口已在等待期耗尽）→ 首轮终检即捕获。"""
        deps = FakeDeps()
        deps.kill_client_in_wait = True
        code = run(_plan(), _settings(), "/x.lock", deps=deps.build())
        assert code == ExitCode.CLIENT_FAILED
        assert "done" not in [e[0] for e in deps.notifier.events]
        # 首轮检查即死亡：零 sleep（确认循环未进入等待）
        assert deps.sleeps == []


class TestSettleSuccess:
    def test_client_survives_full_window(self):
        """窗口内全程存活 → SUCCESS + notify_done；补等轮次 == 窗口/轮询间隔
        （fake wait 瞬完，窗口全额补等——固化轮次制换算）。"""
        deps = FakeDeps()
        code = run(_plan(), _settings(), "/x.lock", deps=deps.build())
        assert code == ExitCode.SUCCESS
        assert deps.notifier.events[-1][0] == "done"
        expected_steps = math.ceil(10.0 / _SETTLE_POLL_S)  # 默认窗口 10s
        assert len(deps.sleeps) == expected_steps
        assert all(s == _SETTLE_POLL_S for s in deps.sleeps)

    def test_settle_disabled_zero(self):
        """client_settle_timeout_s=0 → 跳过确认循环（兼容 T42 前行为）。"""
        deps = FakeDeps()
        code = run(
            _plan(),
            _settings(client_settle_timeout_s=0),
            "/x.lock",
            deps=deps.build(),
        )
        assert code == ExitCode.SUCCESS
        assert deps.sleeps == []


class TestIdempotentSkipsSettle:
    def test_existing_client_no_settle(self):
        """既有 Client 实例（幂等命中）→ 零 sleep、零附加等待
        （🔴 15:10 报告幂等性能红线固化）。"""
        deps = FakeDeps()
        deps.discover_results = {
            "service": DiscoveryResult(ComponentStatus.RUNNING, pid=111),
            "client": DiscoveryResult(ComponentStatus.RUNNING, pid=222),
        }
        code = run(_plan(), _settings(), "/x.lock", deps=deps.build())
        assert code == ExitCode.SUCCESS
        assert deps.spawned == []
        assert deps.sleeps == []
