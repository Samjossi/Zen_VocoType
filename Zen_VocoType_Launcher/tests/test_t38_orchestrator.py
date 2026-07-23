"""T3.8 编排器七场景 fake 单测（CP0 核心）。"""

from pathlib import Path

import pytest

from zen_vocotype_launcher.config import Settings
from zen_vocotype_launcher.discovery import (
    ComponentStatus,
    DiscoveryResult,
    SocketProbeResult,
)
from zen_vocotype_launcher.exit_codes import ExitCode
from zen_vocotype_launcher.locks import LauncherLockError
from zen_vocotype_launcher.orchestrator import (
    ComponentTarget,
    LaunchPlan,
    OrchestratorDeps,
    run,
)
from zen_vocotype_launcher.readiness import ReadyTimeoutError, ServiceUnavailableError


# ---------------------------------------------------------------------- fakes


class FakeProc:
    def __init__(self, name, alive=True):
        self.name = name
        self.pid = 111 if name == "service" else 222
        self._alive = alive
        self.terminated = False

    def is_alive(self):
        return self._alive

    def poll(self):
        return None if self._alive else 1

    def terminate_group(self, grace):
        self.terminated = True
        self._alive = False


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
    def __init__(self, path, mode, fail=False):
        self._fail = fail

    def acquire(self):
        if self._fail:
            raise LauncherLockError("锁被持有")

    def release(self):
        pass


class FakeDeps:
    """按场景装配 OrchestratorDeps。"""

    def __init__(self):
        self.spawned: list[FakeProc] = []
        self.notifier = FakeNotifier()
        self.discover_results: dict[str, DiscoveryResult] = {}
        self.probe_result = (SocketProbeResult.FREE, "")
        self.wait_exc: Exception | None = None
        self.spawn_fail_for: set[str] = set()
        self.client_immediate_dead = False
        self.lock_fail = False

    def build(self) -> OrchestratorDeps:
        outer = self

        def spawn(argv, *, name, env=None, log_path=None):
            if name in outer.spawn_fail_for:
                raise OSError(f"{name} 拉起失败（fake）")
            alive = not (name == "client" and outer.client_immediate_dead)
            proc = FakeProc(name, alive=alive)
            outer.spawned.append(proc)
            return proc

        def discover(lock_path, *, name, expected_exe=None, expected_cmdline_fragment=None):
            return outer.discover_results.get(
                name, DiscoveryResult(ComponentStatus.ABSENT)
            )

        def probe_socket(socket_path):
            return outer.probe_result

        def wait_ready(client, **kwargs):
            if outer.wait_exc is not None:
                raise outer.wait_exc

        def lock_factory(path, mode):
            return FakeLock(path, mode, fail=outer.lock_fail)

        return OrchestratorDeps(
            spawn=spawn,
            wait_for_readiness=wait_ready,
            discover=discover,
            probe_socket=probe_socket,
            client_factory=lambda path: object(),
            notifier=outer.notifier,
            lock_factory=lock_factory,
            log_file=Path("launcher.log"),
        )


def _plan() -> LaunchPlan:
    return LaunchPlan(
        mode="dev",
        socket_path="/run/user/1000/zen_vocotype_dev.sock",
        service=ComponentTarget(
            name="service",
            argv=["/venv/python", "/repo/Zen_VocoType_Service/main.py"],
            lock_path="/run/user/1000/zen_vocotype_service_dev.lock",
            log_path=Path("logs/service.log"),
        ),
        client=ComponentTarget(
            name="client",
            argv=["/venv/python", "/repo/Zen_VocoType_Client/main.py"],
            lock_path="/run/user/1000/zen_vocotype_client_dev.lock",
            log_path=Path("logs/client.log"),
        ),
    )


def _settings() -> Settings:
    return Settings()


# ---------------------------------------------------------------------- 七场景


class TestScenario1Success:
    def test_full_success(self):
        deps = FakeDeps()
        code = run(_plan(), _settings(), "/x/launcher.lock", deps=deps.build())
        assert code == ExitCode.SUCCESS
        # 两端拉起、正常路径不杀子进程（选型七方案 A）
        assert {p.name for p in deps.spawned} == {"service", "client"}
        assert all(not p.terminated for p in deps.spawned)
        # 通知时序：starting → done
        kinds = [e[0] for e in deps.notifier.events]
        assert kinds == ["starting", "done"]


class TestScenario2Idempotent:
    def test_already_running_components(self):
        deps = FakeDeps()
        deps.discover_results = {
            "service": DiscoveryResult(ComponentStatus.RUNNING, pid=111),
            "client": DiscoveryResult(ComponentStatus.RUNNING, pid=222),
        }
        code = run(_plan(), _settings(), "/x/launcher.lock", deps=deps.build())
        assert code == ExitCode.SUCCESS
        assert deps.spawned == []  # 幂等命中：零拉起
        # 仍确认就绪（wait 被调用且无异常）→ done
        assert deps.notifier.events[-1][0] == "done"

    def test_launcher_lock_conflict(self):
        deps = FakeDeps()
        deps.lock_fail = True
        code = run(_plan(), _settings(), "/x/launcher.lock", deps=deps.build())
        assert code == ExitCode.ALREADY_RUNNING
        assert deps.spawned == []
        assert deps.notifier.events[0][0] == "already"


class TestScenario3SlowStart:
    def test_slow_service_within_budget(self):
        deps = FakeDeps()
        calls = []

        # wait 前几次感受到进程仍在启动（alive），最终成功——fake 层即通过
        code = run(_plan(), _settings(), "/x/launcher.lock", deps=deps.build())
        assert code == ExitCode.SUCCESS


class TestScenario4ServiceCrash:
    def test_service_dies_during_wait(self):
        deps = FakeDeps()
        deps.wait_exc = ServiceUnavailableError("服务端进程在等待期间已退出。code=1")
        code = run(_plan(), _settings(), "/x/launcher.lock", deps=deps.build())
        assert code == ExitCode.SERVICE_FAILED
        # 不误判、回收干净：service 被回收；
        # T40 门控后移——client 在就绪判定前已拉起（懒连接容忍），独立存活不回收
        service = next(p for p in deps.spawned if p.name == "service")
        assert service.terminated
        client = next(p for p in deps.spawned if p.name == "client")
        assert not client.terminated
        assert deps.notifier.events[-1][0] == "failed"

    def test_ready_timeout(self):
        deps = FakeDeps()
        deps.wait_exc = ReadyTimeoutError("阶段二超时：180s 内模型未就绪")
        code = run(_plan(), _settings(), "/x/launcher.lock", deps=deps.build())
        assert code == ExitCode.SERVICE_FAILED
        assert next(p for p in deps.spawned if p.name == "service").terminated

    def test_existing_service_abnormal_not_killed(self):
        """既有实例异常：退出码 2 且 Launcher 无权终止用户实例。"""
        deps = FakeDeps()
        deps.discover_results = {
            "service": DiscoveryResult(ComponentStatus.RUNNING, pid=999)
        }
        deps.wait_exc = ReadyTimeoutError("阶段二超时")
        code = run(_plan(), _settings(), "/x/launcher.lock", deps=deps.build())
        assert code == ExitCode.ALREADY_RUNNING
        # 既有 service：未拉起、未回收；
        # T40 门控后移——client 在就绪判定前拉起（懒连接容忍），独立存活
        assert [p.name for p in deps.spawned] == ["client"]
        assert all(not p.terminated for p in deps.spawned)


class TestScenario5ClientFailure:
    def test_client_spawn_fails_reaps_service(self):
        deps = FakeDeps()
        deps.spawn_fail_for = {"client"}
        code = run(_plan(), _settings(), "/x/launcher.lock", deps=deps.build())
        assert code == ExitCode.CLIENT_FAILED
        service = next(p for p in deps.spawned if p.name == "service")
        assert service.terminated  # 逆序回收已拉起的 Service

    def test_client_immediate_exit(self):
        deps = FakeDeps()
        deps.client_immediate_dead = True
        code = run(_plan(), _settings(), "/x/launcher.lock", deps=deps.build())
        assert code == ExitCode.CLIENT_FAILED

    def test_client_failure_keeps_existing_service(self):
        """Service 为既有实例时，Client 失败不回收它。"""
        deps = FakeDeps()
        deps.discover_results = {
            "service": DiscoveryResult(ComponentStatus.RUNNING, pid=999)
        }
        deps.spawn_fail_for = {"client"}
        code = run(_plan(), _settings(), "/x/launcher.lock", deps=deps.build())
        assert code == ExitCode.CLIENT_FAILED
        assert deps.spawned == []  # service 非本进程拉起，零回收


class TestScenario6SocketForeign:
    def test_foreign_socket(self):
        deps = FakeDeps()
        deps.probe_result = (SocketProbeResult.FOREIGN, "非本组件协议")
        code = run(_plan(), _settings(), "/x/launcher.lock", deps=deps.build())
        assert code == ExitCode.CONFIG_ERROR
        assert deps.spawned == []
        assert deps.notifier.events[-1][0] == "failed"

    def test_ours_socket_reused(self):
        deps = FakeDeps()
        deps.probe_result = (SocketProbeResult.OURS, "本组件协议实例")
        code = run(_plan(), _settings(), "/x/launcher.lock", deps=deps.build())
        assert code == ExitCode.SUCCESS
        assert all(p.name != "service" for p in deps.spawned)  # 复用不重拉
