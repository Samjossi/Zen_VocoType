"""Zen_VocoType 通信协议契约库。

唯一共享物：Service / Client / Launcher 以包依赖方式引用本库，
禁止三组件文件夹之间任何形式的相对路径 import。
"""

from zen_vocotype_protocol import actions, errors, frames, paths, settings, version

__all__ = ["actions", "errors", "frames", "paths", "settings", "version"]
