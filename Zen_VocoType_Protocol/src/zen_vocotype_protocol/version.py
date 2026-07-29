"""协议版本号（版本握手用）。

两端 `health` 握手时交换本版本号；不一致时客户端必须明确报错，
🔴 禁止静默继续（重写大纲 §5-5：禁止任何静默降级）。

版本号为两位数格式 major.minor（如 "1.0"），唯一真相为仓库根目录
``versions.toml``，本文件由 ``tools/sync_versions.py`` 同步，🔴 禁止手改版本行。
"""

#: 协议版本号（两位数 major.minor）；帧格式/action/错误码的任何不兼容变更必须升版本
PROTOCOL_VERSION: str = "1.2"


def parse_version(text: str) -> tuple[int, int]:
    """将 ``"1.0"`` 形式的版本号解析为 ``(major, minor)`` 元组。

    :raises ValueError: 格式非法时抛出，调用方必须显式处理（禁止静默降级）。
    """
    parts = text.split(".")
    if len(parts) != 2:
        raise ValueError(f"非法协议版本号: {text!r}")
    try:
        return int(parts[0]), int(parts[1])
    except ValueError:
        raise ValueError(f"非法协议版本号: {text!r}") from None


def is_compatible(local: str, remote: str) -> bool:
    """判断两端协议版本是否兼容。

    两位数格式下 major.minor 即版本号全部，兼容 = 完全相等。
    后续如需放宽（如 minor 差异放行）在此集中修改（单一出处）。
    """
    return parse_version(local) == parse_version(remote)
