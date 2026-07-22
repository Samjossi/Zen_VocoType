"""T3.9 目标解析测试：dev 布局解析、正式解析三分支、环境注入。"""

import os
import sys
from pathlib import Path

import pytest

from zen_vocotype_launcher.config import Settings
from zen_vocotype_launcher.targets import (
    TargetResolutionError,
    _child_env,
    _repo_root,
    build_plan,
)


class TestDevPlan:
    def test_repo_layout_resolves(self):
        plan = build_plan(Settings(), dev_mode=True)
        assert plan.mode == "dev"
        repo = _repo_root()
        assert (repo / ".venv" / "bin" / "python").is_file()
        assert plan.service.argv[1] == str(repo / "Zen_VocoType_Service" / "main.py")
        assert plan.client.argv[1] == str(repo / "Zen_VocoType_Client" / "main.py")
        # 两端入口真实存在（自定位解析，非 cwd）
        assert Path(plan.service.argv[1]).is_file()
        assert Path(plan.client.argv[1]).is_file()

    def test_dev_uses_dev_socket_and_locks(self):
        from zen_vocotype_protocol.paths import (
            DEV_CLIENT_LOCK_PATH,
            DEV_SERVICE_LOCK_PATH,
            DEV_SOCKET_PATH,
        )

        plan = build_plan(Settings(), dev_mode=True)
        assert plan.socket_path == DEV_SOCKET_PATH
        assert plan.service.lock_path == DEV_SERVICE_LOCK_PATH
        assert plan.client.lock_path == DEV_CLIENT_LOCK_PATH

    def test_dev_env_injection(self):
        from zen_vocotype_protocol.paths import DEV_SOCKET_PATH

        plan = build_plan(Settings(), dev_mode=True)
        env = plan.service.env
        assert env["ZEN_VOCOTYPE_SERVICE_SOCKET_PATH"] == DEV_SOCKET_PATH
        assert env["ZEN_VOCOTYPE_CLIENT_SOCKET_PATH"] == DEV_SOCKET_PATH

    def test_dev_exe_expectation(self):
        plan = build_plan(Settings(), dev_mode=True)
        assert plan.service.expected_exe.endswith("python") or "python" in plan.service.expected_exe
        assert plan.service.expected_cmdline_fragment.endswith("main.py")


class TestProdPlan:
    def test_explicit_binary(self, tmp_path):
        svc = tmp_path / "svc"
        svc.touch()
        cli = tmp_path / "cli"
        cli.touch()
        settings = Settings(service_binary=str(svc), client_binary=str(cli))
        plan = build_plan(settings, dev_mode=False)
        assert plan.mode == "prod"
        assert plan.service.argv == [str(svc)]
        assert plan.client.argv == [str(cli)]

    def test_explicit_missing(self):
        settings = Settings(service_binary="/nonexistent/svc", client_binary="/nonexistent/cli")
        with pytest.raises(TargetResolutionError, match="不存在"):
            build_plan(settings, dev_mode=False)

    def test_relative_explicit_rejected(self):
        settings = Settings(service_binary="./svc", client_binary="/x")
        with pytest.raises(TargetResolutionError, match="绝对路径"):
            build_plan(settings, dev_mode=False)

    def test_sibling_convention(self, tmp_path, monkeypatch):
        # 邻接目录约定：Launcher 同目录存在两端二进制
        svc = tmp_path / "Zen_VocoType_Service.AppImage"
        svc.touch()
        cli = tmp_path / "Zen_VocoType_Client.AppImage"
        cli.touch()
        monkeypatch.setattr(sys, "argv", [str(tmp_path / "Zen_VocoType_Launcher.AppImage")])
        plan = build_plan(Settings(), dev_mode=False)
        assert plan.service.argv == [str(svc)]
        assert plan.client.argv == [str(cli)]

    def test_sibling_onedir_convention(self, tmp_path, monkeypatch):
        """邻接目录 onedir 形态：目录名/同名二进制（T4.4 回填，tools/build.py 布局）。"""
        svc_dir = tmp_path / "zen_vocotype_service"
        svc_dir.mkdir()
        svc = svc_dir / "zen_vocotype_service"
        svc.touch()
        cli_dir = tmp_path / "zen_vocotype_client"
        cli_dir.mkdir()
        cli = cli_dir / "zen_vocotype_client"
        cli.touch()
        monkeypatch.setattr(sys, "argv", [str(tmp_path / "Zen_VocoType_Launcher.AppImage")])
        plan = build_plan(Settings(), dev_mode=False)
        assert plan.service.argv == [str(svc)]
        assert plan.client.argv == [str(cli)]

    def test_sibling_via_appimage_env(self, tmp_path, monkeypatch):
        """AppImage 形态：邻接基准取 APPIMAGE 环境变量目录而非 argv[0] 挂载点。

        （T4.4 联调实测：argv[0] 为 /tmp/.mount_*/usr/... 载荷路径，邻接失效）
        """
        svc = tmp_path / "Zen_VocoType_Service.AppImage"
        svc.touch()
        cli = tmp_path / "Zen_VocoType_Client.AppImage"
        cli.touch()
        monkeypatch.setenv("APPIMAGE", str(tmp_path / "Zen_VocoType_Launcher.AppImage"))
        # argv[0] 模拟挂载点内载荷路径——应被 APPIMAGE 分支覆盖
        monkeypatch.setattr(sys, "argv", ["/tmp/.mount_Fake12/usr/zen_vocotype_launcher/zen_vocotype_launcher"])
        plan = build_plan(Settings(), dev_mode=False)
        assert plan.service.argv == [str(svc)]
        assert plan.client.argv == [str(cli)]

    def test_missing_everywhere(self, tmp_path, monkeypatch):
        monkeypatch.setattr(sys, "argv", [str(tmp_path / "launcher")])
        with pytest.raises(TargetResolutionError, match="未找到 service 二进制"):
            build_plan(Settings(), dev_mode=False)

    def test_prod_locks_and_socket(self, tmp_path):
        from zen_vocotype_protocol.paths import (
            CLIENT_LOCK_PATH,
            DEFAULT_SOCKET_PATH,
            SERVICE_LOCK_PATH,
        )

        svc = tmp_path / "svc"
        svc.touch()
        cli = tmp_path / "cli"
        cli.touch()
        settings = Settings(service_binary=str(svc), client_binary=str(cli))
        plan = build_plan(settings, dev_mode=False)
        assert plan.socket_path == DEFAULT_SOCKET_PATH
        assert plan.service.lock_path == SERVICE_LOCK_PATH
        assert plan.client.lock_path == CLIENT_LOCK_PATH


class TestMainCli:
    def test_help_exits_zero(self):
        import subprocess

        result = subprocess.run(
            [sys.executable, "Zen_VocoType_Launcher/main.py", "--help"],
            capture_output=True,
            text=True,
            cwd=_repo_root(),
        )
        assert result.returncode == 0
        assert "--dev" in result.stdout
