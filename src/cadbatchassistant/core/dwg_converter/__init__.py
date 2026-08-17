"""DWG <-> DXF 转换引擎抽象与 ODA File Converter 实现。

按职责拆分：
- protocol : Converter 转换引擎接口（Protocol）+ ODAError + DEFAULT_OUT_VERSION
- oda      : OdaConverter 实现（探测 / 命令行调用 / 产物轮询）

公共 API 在本包顶层统一导出，调用方以
`from cadbatchassistant.core import dwg_converter as dc` 或
`from cadbatchassistant.core.dwg_converter import ...` 形式使用。
"""

from __future__ import annotations

import subprocess

from cadbatchassistant.core.dwg_converter.oda import OdaConverter
from cadbatchassistant.core.dwg_converter.protocol import (
    DEFAULT_OUT_VERSION,
    Converter,
    ODAError,
)


def get_converter(kind: str = "oda") -> Converter:
    """返回 DWG 转换引擎实现（工厂）。

    kind 为引擎标识（当前仅 "oda"）；业务层应经本函数获取实现而非
    直接实例化，以便未来新增引擎（如 Teigha / libredwg）时无侵入切换。
    """
    if kind == "oda":
        return OdaConverter()
    raise ValueError(f"未知的 DWG 转换引擎: {kind}")


__all__ = [
    "DEFAULT_OUT_VERSION",
    "Converter",
    "ODAError",
    "OdaConverter",
    "get_converter",
    # 供测试引用的标准库模块引用（保持 dc.xxx 命名空间）
    "subprocess",
]
