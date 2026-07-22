#!/usr/bin/env python3
"""版本同步脚本：versions.toml（唯一真相）→ 各组件本地出处。

用法（仓库根目录，项目 .venv 解释器）：

    .venv/bin/python tools/sync_versions.py

规则：
- 只改各组件本地出处文件中的版本行，文件其他内容一行不动；
- 出处文件缺失、版本行未命中或多次命中 → 报错退出非零（🔴 禁止静默跳过）；
- 全部改写后重新读取校验一遍（二次确认），任何不一致即失败。
"""

from __future__ import annotations

import re
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VERSIONS_TOML = ROOT / "versions.toml"

#: 两位数版本号规范（major.minor，如 "1.0"）
VERSION_RE = re.compile(r"^\d+\.\d+$")

#: 组件键 → (本地出处文件相对路径, 版本行正则, 版本行模板)
TARGETS: dict[str, tuple[str, re.Pattern[str], str]] = {
    "client": (
        "Zen_VocoType_Client/src/zen_vocotype_client/__init__.py",
        re.compile(r'^__version__ = "[^"]+"$'),
        '__version__ = "{version}"',
    ),
    "launcher": (
        "Zen_VocoType_Launcher/src/zen_vocotype_launcher/version.py",
        re.compile(r'^LAUNCHER_VERSION: str = "[^"]+"$'),
        'LAUNCHER_VERSION: str = "{version}"',
    ),
    "protocol": (
        "Zen_VocoType_Protocol/src/zen_vocotype_protocol/version.py",
        re.compile(r'^PROTOCOL_VERSION: str = "[^"]+"$'),
        'PROTOCOL_VERSION: str = "{version}"',
    ),
    "service": (
        "Zen_VocoType_Service/src/zen_vocotype_service/version.py",
        re.compile(r'^SERVICE_VERSION: str = "[^"]+"$'),
        'SERVICE_VERSION: str = "{version}"',
    ),
}


def fail(message: str) -> None:
    print(f"错误: {message}", file=sys.stderr)
    sys.exit(1)


def load_versions() -> dict[str, str]:
    """读取并校验 versions.toml，返回 {组件: 版本号}。"""
    if not VERSIONS_TOML.exists():
        fail(f"唯一真相文件不存在: {VERSIONS_TOML}")
    with VERSIONS_TOML.open("rb") as f:
        data = tomllib.load(f)
    components = data.get("components")
    if not isinstance(components, dict):
        fail("versions.toml 缺少 [components] 表")
    missing = sorted(set(TARGETS) - set(components))
    if missing:
        fail(f"versions.toml 缺少组件键: {missing}")
    for name, version in components.items():
        if not isinstance(version, str) or not VERSION_RE.match(version):
            fail(f"组件 {name} 版本号非法: {version!r}（须为两位数 major.minor，如 \"1.0\"）")
    return {name: components[name] for name in TARGETS}


def sync_component(name: str, version: str) -> bool:
    """同步单个组件的本地出处，返回是否发生改写。"""
    rel, pattern, template = TARGETS[name]
    path = ROOT / rel
    if not path.exists():
        fail(f"{name}: 本地出处文件不存在: {rel}")
    lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
    hits = [i for i, line in enumerate(lines) if pattern.match(line.rstrip("\n"))]
    if len(hits) != 1:
        fail(f"{name}: 版本行命中 {len(hits)} 次（应为 1）: {rel}")
    index = hits[0]
    new_line = template.format(version=version)
    old_line = lines[index].rstrip("\n")
    if old_line == new_line:
        print(f"  {name}: 已是 {version}，无需改动  ({rel})")
        return False
    lines[index] = new_line + ("\n" if lines[index].endswith("\n") else "")
    path.write_text("".join(lines), encoding="utf-8")
    print(f"  {name}: {old_line}  →  {new_line}  ({rel})")
    return True


def verify(versions: dict[str, str]) -> None:
    """二次校验：改写后重新读取各出处，版本行必须唯一且与 versions.toml 一致。"""
    for name, version in versions.items():
        rel, pattern, _ = TARGETS[name]
        path = ROOT / rel
        lines = path.read_text(encoding="utf-8").splitlines()
        hits = [line for line in lines if pattern.match(line)]
        if len(hits) != 1 or f'"{version}"' not in hits[0]:
            fail(f"{name}: 改写后校验失败（{rel}），请检查文件内容")


def main() -> int:
    print(f"读取唯一真相: {VERSIONS_TOML}")
    versions = load_versions()
    changed = sum(sync_component(name, version) for name, version in versions.items())
    verify(versions)
    print(f"完成：{changed} 处改写，{len(versions)} 个组件校验通过。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
