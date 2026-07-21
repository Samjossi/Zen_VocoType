"""协议版本号（版本握手用）。

两端 `health` 握手时交换本版本号；不一致时客户端必须明确报错，
🔴 禁止静默继续（重写大纲 §5-5：禁止任何静默降级）。
"""

#: 协议版本号，遵循语义化版本；帧格式/action/错误码的任何不兼容变更必须升 MINOR 以上
PROTOCOL_VERSION: str = "1.0.0"


def parse_version(text: str) -> tuple[int, int, int]:
    """将 ``"1.0.0"`` 形式的版本号解析为 ``(major, minor, patch)`` 元组。

    :raises ValueError: 格式非法时抛出，调用方必须显式处理（禁止静默降级）。
    """
    parts = text.split(".")
    if len(parts) != 3:
        raise ValueError(f"非法协议版本号: {text!r}")
    try:
        return int(parts[0]), int(parts[1]), int(parts[2])
    except ValueError:
        raise ValueError(f"非法协议版本号: {text!r}") from None


def is_compatible(local: str, remote: str) -> bool:
    """判断两端协议版本是否兼容。

    v1 阶段采用严格策略：MAJOR.MINOR 完全一致才视为兼容，
    patch 差异放行。后续如需放宽在此集中修改（单一出处）。
    """
    l_major, l_minor, _ = parse_version(local)
    r_major, r_minor, _ = parse_version(remote)
    return (l_major, l_minor) == (r_major, r_minor)
