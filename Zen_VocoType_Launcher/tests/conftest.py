"""Launcher 测试公共夹具。"""

import pytest


@pytest.fixture(autouse=True)
def _isolate_user_config(tmp_path, monkeypatch):
    """隔离宿主用户配置：实机 ``user_config.yaml`` 的设置项会经契约库
    用户配置层渗入 ``Settings()``，污染默认值断言与「内存未变」断言
    （T35 同模板：常量于 paths 模块导入期冻结，monkeypatch
    XDG_CONFIG_HOME 环境变量无效，须冻结常量本体）。
    """
    monkeypatch.setattr(
        "zen_vocotype_protocol.paths.DEFAULT_USER_CONFIG_PATH",
        tmp_path / "zen_vocotype" / "user_config.yaml",
    )
