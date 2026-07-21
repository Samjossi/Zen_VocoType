"""T1.4 单元测试：MODELSCOPE_CACHE 导入顺序固化（选型文档 §11-2）。

main.py 必须在任何 funasr/modelscope 导入之前设置 MODELSCOPE_CACHE 指向
``Settings.models_dir``；该顺序以子进程实测固化，防止后续重构破坏。
"""

import subprocess
import sys
import textwrap


def test_modelscope_cache_set_before_funasr_import():
    code = textwrap.dedent(
        """
        import os, sys
        import main  # Zen_VocoType_Service/main.py
        from zen_vocotype_service.config import Settings

        expected = str(Settings().models_dir)
        actual = os.environ.get("MODELSCOPE_CACHE")
        assert actual == expected, f"MODELSCOPE_CACHE={actual!r} != {expected!r}"
        # 此刻 funasr/modelscope 必须尚未被导入（顺序敏感红线）
        leaked = [m for m in sys.modules if m.split(".")[0] in ("funasr", "modelscope")]
        assert not leaked, f"main 导入时 funasr/modelscope 已被提前加载: {leaked}"
        print("ENV_ORDER_OK")
        """
    )
    proc = subprocess.run(
        [sys.executable, "-c", code],
        cwd=str(__import__("pathlib").Path(__file__).resolve().parents[1]),
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert "ENV_ORDER_OK" in proc.stdout, (
        f"stdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"
    )
