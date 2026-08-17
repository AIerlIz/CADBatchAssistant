"""DWG<->DXF 转换引擎接口（Converter Protocol）与共享常量。

业务层只依赖本协议与 DEFAULT_OUT_VERSION，通过
dwg_converter.get_converter() 工厂获取实现，未来可无侵入地切换其他引擎
（如 Teigha / libredwg 封装）。
"""

from __future__ import annotations

from pathlib import Path
from typing import Protocol

DEFAULT_OUT_VERSION = "ACAD2018"


class ODAError(RuntimeError):
    """ODA 转换失败。"""


class Converter(Protocol):
    """DWG<->DXF 转换引擎接口：业务层只依赖本协议，实现可切换。"""

    name: str

    def find(self) -> Path | None:
        """探测可用的转换器可执行文件路径；未找到返回 None。"""
        ...

    def resolve(self, oda: str | Path | None = None) -> str:
        """返回可用的转换器路径（字符串）；未显式指定时自动探测。"""
        ...

    def require_for_dwg(self, has_dwg: bool, oda: str) -> str | None:
        """需处理 DWG 但未配置有效转换器时返回错误文案；否则返回 None。"""
        ...

    def convert_batch(
        self,
        oda_exe: str | Path,
        in_dir: str | Path,
        out_dir: str | Path,
        out_version: str = DEFAULT_OUT_VERSION,
        out_type: str = "DXF",
        recursive: int = 0,
        timeout: float = 900,
        input_filter: str | None = None,
    ) -> None:
        """调用转换器批量转换，阻塞直至进程退出。"""
        ...

    def wait_for_outputs(
        self,
        out_dir: str | Path,
        expected_names: set[str],
        timeout: float = 120,
        interval: float = 2,
    ) -> None:
        """轮询输出目录，直到全部期望文件存在或超时。"""
        ...

    def template_to_dxf(
        self,
        template: str | Path,
        workdir: str | Path,
        oda: str | Path = "",
        out_version: str = DEFAULT_OUT_VERSION,
        subdir: str = "_tmpl_dxf",
    ) -> str:
        """把图纸模板（.dwg/.dxf）转成 DXF，返回模板 DXF 路径。"""
        ...

    def dwg_to_dxf(
        self,
        oda_exe: str | Path,
        in_dir: str | Path,
        out_dir: str | Path,
        dwg_names: list[str],
        out_version: str = DEFAULT_OUT_VERSION,
        timeout: float = 900,
    ) -> None:
        """把一批 DWG 转成同名的 DXF，并确认全部产物生成。"""
        ...

    def dxf_to_dwg(
        self,
        oda_exe: str | Path,
        in_dir: str | Path,
        out_dir: str | Path,
        dxf_names: list[str],
        out_version: str = DEFAULT_OUT_VERSION,
        timeout: float = 900,
    ) -> None:
        """把一批 DXF 转成同名的 DWG，并确认全部产物生成。"""
        ...
