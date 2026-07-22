"""paths 模块测试：XDG 目录解析与录音保存目录唯一出处（T34）。"""

from pathlib import Path

from zen_vocotype_protocol import paths


class TestGetRecordingsDir:
    def test_default_fallback(self, monkeypatch):
        """未设 XDG_DATA_HOME 时回退 ~/.local/share/zen_vocotype/recordings。"""
        monkeypatch.delenv("XDG_DATA_HOME", raising=False)
        assert paths.get_recordings_dir() == (
            Path.home() / ".local" / "share" / "zen_vocotype" / "recordings"
        )

    def test_xdg_data_home_respected(self, monkeypatch, tmp_path):
        """XDG_DATA_HOME 优先，且为运行时解析（非导入期冻结）。"""
        monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
        assert paths.get_recordings_dir() == tmp_path / "zen_vocotype" / "recordings"

    def test_absolute_path(self):
        assert paths.get_recordings_dir().is_absolute()
