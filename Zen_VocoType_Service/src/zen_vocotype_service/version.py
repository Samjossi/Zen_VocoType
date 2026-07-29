"""服务端软件版本号（health 响应与托盘版本项用，单一出处）。

版本号为两位数格式 major.minor（🔴 未来版本延续 1.1、1.2、2.0…，
不再使用三段式），唯一真相为仓库根目录 ``versions.toml``，
本文件由 ``tools/sync_versions.py`` 同步，🔴 禁止手改版本行。
"""

SERVICE_VERSION: str = "1.4"
