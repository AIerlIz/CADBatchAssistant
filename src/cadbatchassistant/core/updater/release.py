"""GitHub 最新 Release 查询。

- _request_json : GET JSON（带 UA、响应体大小上限），失败转 UpdateError
- _find_assets  : 从 assets 列表定位安装包与 sha256 校验和资产
- check_latest  : 查询最新 Release 并返回版本信息或失败原因
"""

from __future__ import annotations

import http.client
import json
import urllib.error
import urllib.request

from cadbatchassistant.core.updater.constants import (
    API_TIMEOUT,
    ASSET_NAME,
    GITHUB_REPO,
    MAX_RESPONSE_BYTES,
    SHA256_ASSET_NAME,
    USER_AGENT,
    UpdateError,
)
from cadbatchassistant.core.updater.version import parse_version


def _request_json(url: str, timeout: int = API_TIMEOUT) -> dict:
    """GET JSON（带 UA），失败时抛出带用户可读信息的 UpdateError。"""
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read(MAX_RESPONSE_BYTES + 1)
            if len(body) > MAX_RESPONSE_BYTES:
                raise UpdateError("更新信息响应过大，已中止读取")  # noqa: TRY301
    except UpdateError:
        raise
    except urllib.error.HTTPError as e:
        raise UpdateError(f"服务器返回 {e.code}（{e.reason}）") from e
    except (
        TimeoutError,
        urllib.error.URLError,
        OSError,
        http.client.HTTPException,
    ) as e:
        raise UpdateError(f"无法连接 GitHub：{e}") from e
    try:
        data = json.loads(body)
    except (ValueError, UnicodeDecodeError) as e:
        raise UpdateError("更新信息解析失败") from e
    if not isinstance(data, dict):
        raise UpdateError("更新信息格式异常")
    return data


def _find_assets(assets: list) -> tuple[str | None, int | None, str | None]:
    """从 GitHub assets 列表定位安装包与校验和资产。

    返回 (browser_download_url, size, sha256_url)；两者都命中即可提前结束。
    """
    url, size, sha256_url = None, None, None
    for asset in assets:
        name = asset.get("name")
        if name == ASSET_NAME:
            url = asset.get("browser_download_url")
            size = asset.get("size")
        elif name == SHA256_ASSET_NAME:
            sha256_url = asset.get("browser_download_url")
        if url and sha256_url:  # 两者都命中即可提前结束
            break
    return url, size, sha256_url


def check_latest(repo: str = GITHUB_REPO, timeout: int = API_TIMEOUT) -> dict:
    """查询 GitHub 最新 Release。

    返回：
    - 成功: {"ok": True, "tag": "v1.1.0", "version": (1,1,0),
             "url": "...", "size": 123456, "sha256_url": "..."}
    - 失败: {"ok": False, "error": "用户可读原因"}
    """
    try:
        data = _request_json(
            f"https://api.github.com/repos/{repo}/releases/latest", timeout
        )
    except UpdateError as e:
        return {"ok": False, "error": str(e)}

    tag = str(data.get("tag_name", ""))
    version = parse_version(tag)
    if version is None:
        return {"ok": False, "error": f"无法解析版本号：{tag}"}

    assets = data.get("assets")
    if not isinstance(assets, list):
        assets = []
    url, size, sha256_url = _find_assets(assets)
    if not url:
        return {"ok": False, "error": f"最新版本未包含安装包 {ASSET_NAME}"}
    if not sha256_url:
        # 更新强制 sha256 强校验：缺少校验和资产的 Release 视为不可安全更新
        return {"ok": False, "error": f"最新版本未包含校验和资产 {SHA256_ASSET_NAME}"}

    return {
        "ok": True,
        "tag": tag,
        "version": version,
        "url": str(url),
        "size": int(size) if size else None,
        "sha256_url": str(sha256_url),
    }
