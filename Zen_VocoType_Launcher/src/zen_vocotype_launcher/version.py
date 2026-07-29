"""启动器软件版本号（单一出处）。

版本号为两位数格式 major.minor（如 "0.1"），唯一真相为仓库根目录
``versions.toml``，本文件由 ``tools/sync_versions.py`` 同步，🔴 禁止手改版本行。
pyproject.toml 经 hatch pattern 动态读取本值。
"""

LAUNCHER_VERSION: str = "1.3"
