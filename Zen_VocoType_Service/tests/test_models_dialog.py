"""托盘「模型清单…」单元测试。

HTML 构造（纯函数）/ 缓存状态探测 / 对话框装配 / 菜单接入。

🔴 offscreen 平台必须在 PySide6 导入前设置（headless CI 兼容）。
"""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtWidgets import QApplication, QTextBrowser

from zen_vocotype_service.config import ModelEntry, Settings
from zen_vocotype_service.context import ServiceContext
from zen_vocotype_service.state import ServiceState
from zen_vocotype_service.tray.models_dialog import (
    build_models_html,
    cache_status,
    create_models_dialog,
)
from zen_vocotype_service.tray.tray import ServiceTray


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


class TestDescriptionField:
    def test_default_empty(self):
        """用户自建条目未写描述时默认空串（不破坏既有条目）。"""
        entry = ModelEntry(model_id="iic/X")
        assert entry.description == ""

    def test_builtin_entries_have_description(self):
        settings = Settings()
        for name, entry in settings.models.items():
            assert entry.description, f"内置条目 {name} 缺描述"

    def test_builtin_registry_has_three_models(self):
        """内置注册表定稿三条：通用默认 / 多语言情感 / 高精度实验性。"""
        assert set(Settings().models) == {
            "fun-asr-nano",
            "sensevoice-small",
            "qwen3-asr-1.7b",
        }


class TestCacheStatus:
    def test_local_path_entry_needs_no_cache(self, tmp_path):
        entry = ModelEntry(local_path=tmp_path)
        assert cache_status(entry, tmp_path) == "本地直载（无需下载）"

    def test_uncached_model_id(self, tmp_path):
        entry = ModelEntry(model_id="iic/SenseVoiceSmall")
        assert "未缓存" in cache_status(entry, tmp_path)
        assert "自动下载" in cache_status(entry, tmp_path)

    def test_cached_model_id(self, tmp_path):
        entry = ModelEntry(model_id="iic/SenseVoiceSmall")
        (tmp_path / "models" / "iic--SenseVoiceSmall").mkdir(parents=True)
        assert cache_status(entry, tmp_path) == "已缓存"


class TestBuildModelsHtml:
    def test_lists_all_registry_models(self):
        settings = Settings()
        html = build_models_html(settings, "fun-asr-nano")
        assert f"共 <b>{len(settings.models)}</b> 个可切换模型" in html
        for name in settings.models:
            assert name in html

    def test_marks_current_model(self):
        html = build_models_html(Settings(), "fun-asr-nano")
        assert "fun-asr-nano" in html and "✅ 当前" in html
        assert "已加载（当前模型）" in html
        assert "未加载" in html  # 非当前模型行

    def test_no_current_model(self):
        html = build_models_html(Settings(), None)
        assert "—（未加载）" in html

    def test_modelscope_link_for_model_id_entries(self):
        settings = Settings()
        html = build_models_html(settings, None)
        for entry in settings.models.values():
            assert f"https://www.modelscope.cn/models/{entry.model_id}" in html

    def test_local_path_entry_has_no_link(self):
        settings = Settings(
            models={"m": ModelEntry(local_path="/data/x", description="d")},
            default_model="m",
        )
        html = build_models_html(settings, None)
        assert "modelscope.cn" not in html
        assert "local_path:/data/x" in html

    def test_empty_description_marked(self):
        settings = Settings(models={"m": ModelEntry(model_id="iic/X")}, default_model="m")
        assert "（未提供描述）" in build_models_html(settings, None)

    def test_html_escapes_description(self):
        settings = Settings(
            models={"m": ModelEntry(model_id="iic/X", description="<script>")},
            default_model="m",
        )
        html = build_models_html(settings, None)
        assert "<script>" not in html
        assert "&lt;script&gt;" in html


class TestDialog:
    def test_constructs_with_content(self, qapp):
        dialog = create_models_dialog(Settings(), "fun-asr-nano")
        assert dialog.windowTitle() == "模型清单"
        browser = dialog.findChild(QTextBrowser)
        assert browser is not None
        assert "fun-asr-nano" in browser.toHtml()
        dialog.close()
        dialog.deleteLater()


class TestTrayMenuIntegration:
    def test_menu_has_models_list_action(self, qapp):
        ctx = ServiceContext(Settings(), ServiceState())
        tray = ServiceTray(ctx, poll_interval_ms=60_000)
        texts = [a.text() for a in tray.tray_icon.contextMenu().actions()]
        assert "模型清单…" in texts
        # 位于「切换模型」子菜单之后、「模型目录」行之前
        assert texts.index("模型清单…") > texts.index("切换模型")
        models_dir_row = next(t for t in texts if t.startswith("模型目录："))
        assert texts.index("模型清单…") < texts.index(models_dir_row)
