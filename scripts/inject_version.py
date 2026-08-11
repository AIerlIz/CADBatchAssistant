"""构建时版本注入脚本：让打包 exe 的版本号自动与发版版本一致。

版本来源优先级：
1. 环境变量 GITHUB_REF_NAME（GitHub Actions push v* tag 时为 tag 名，如 v1.0.1）
2. pyproject.toml 的 [project].version（本地打包 / workflow_dispatch 回退）

把版本写入 src/cadbatchassistant/__init__.py 的 __version__，
应用内「当前版本」显示与在线更新的新旧判断即与 Release 版本一致，
发版时无需再手动同步两处版本号。

用法：uv run python build/inject_version.py [--dry-run]
"""

from __future__ import annotations

import argparse
import os
import re
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
INIT_FILE = ROOT / "src" / "cadbatchassistant" / "__init__.py"
PYPROJECT = ROOT / "pyproject.toml"
_VERSION_RE = re.compile(r'(__version__\s*=\s*)"([^"]*)"')


def version_from_pyproject() -> str | None:
    """读取 pyproject.toml 的 [project].version。"""
    try:
        with open(PYPROJECT, "rb") as f:
            data = tomllib.load(f)
        return data.get("project", {}).get("version")
    except Exception:  # noqa: BLE001 - 缺失/损坏时返回 None
        return None


def resolve_version() -> str | None:
    """确定要注入的版本：CI tag（去 v 前缀）优先，否则 pyproject.toml。"""
    ref = os.environ.get("GITHUB_REF_NAME", "").strip()
    if ref.startswith("v") and len(ref) > 1:
        return ref[1:]
    return version_from_pyproject()


def current_version() -> str | None:
    """读取 __init__.py 当前的 __version__。"""
    try:
        src = INIT_FILE.read_text(encoding="utf-8")
        m = _VERSION_RE.search(src)
        return m.group(2) if m else None
    except FileNotFoundError:
        return None


def inject(version: str) -> bool:
    """写入 __version__；返回是否发生了实际修改。"""
    src = INIT_FILE.read_text(encoding="utf-8")
    new_src, n = _VERSION_RE.subn(
        lambda m: f'{m.group(1)}"{version}"', src, count=1)
    if n == 0:
        raise RuntimeError(f"未在 {INIT_FILE} 中找到 __version__")
    changed = new_src != src
    if changed:
        INIT_FILE.write_text(new_src, encoding="utf-8")
    return changed


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--dry-run", action="store_true", help="只打印不写入")
    args = ap.parse_args()

    version = resolve_version()
    if not version:
        print("ERROR: 无法确定版本（GITHUB_REF_NAME 与 pyproject.toml 均无）",
              file=sys.stderr)
        return 1
    old = current_version()
    if args.dry_run:
        print(f"__version__: {old} -> {version} (dry-run)")
        return 0
    try:
        changed = inject(version)
    except RuntimeError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1
    print(f"__version__: {old} -> {version}"
          + ("（已写入）" if changed else "（无变化）"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
