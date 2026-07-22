# PyInstaller runtime hook：qwen3-asr 依赖裁剪（仅打包形态生效）。
#
# 背景：qwen_asr.inference.qwen3_forced_aligner 顶层 import nagisa，而 nagisa
# ① import 时即实例化 Tagger 加载 46MB 数据文件；② 依赖 dynet（数百 MB 动态库）；
# ③ 包内使用 Python2 式裸导入（import prepro/model），在 PYZ 归档中无法解析。
# 本项目 🔴 永不启用 forced aligner（loader.py 的 _load_qwen3_asr 不传
# forced_aligner），该代码路径为死路径。此处以占位模块使 import nagisa 成功，
# 避免为死路径收编数百 MB 依赖。若未来启用 forced aligner，必须移除此 hook
# 并改为完整收编 nagisa+dynet。
import sys
import types

if "nagisa" not in sys.modules:
    _fake = types.ModuleType("nagisa")

    class _Tagger:  # noqa: D101（占位类，实例化即报真实原因）
        def __init__(self, *args, **kwargs):
            raise RuntimeError(
                "nagisa 未随包收编：forced aligner 在本产品中未启用"
                "（见 tools/specs/rhook_qwen_asr.py）"
            )

    def _fit(*args, **kwargs):
        raise RuntimeError("nagisa 未随包收编（forced aligner 未启用）")

    _fake.Tagger = _Tagger
    _fake.fit = _fit
    sys.modules["nagisa"] = _fake
