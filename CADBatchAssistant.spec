# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec：「CAD批处理助手」统一应用（改字助手 + 填表助手 + 目录助手）。

- 入口：main.py（Notebook 统一窗口，三个功能 tab + 设置；含 --selftest 诊断模式）
- 产物：dist/CADBatchAssistant.exe（单文件、无控制台窗口；与 Release 资产同名，
  本地构建产物与 CI 一致）
- tkinterdnd2：手动收集其 tkdnd 扩展目录（hook-contrib 未覆盖），
  运行时从 os.path.dirname(__file__)/tkdnd/<platform> 加载
- 模板库 templates/fill 与 templates/catalog 由程序在上传时自动创建（exe 同目录）
"""

import os

import tkinterdnd2

_TKDND_SRC = os.path.join(os.path.dirname(tkinterdnd2.__file__), "tkdnd")
_LOGO_PNG = os.path.join(SPECPATH, "assets", "logo.png")
_LOGO_ICO = os.path.join(SPECPATH, "assets", "logo.ico")

a = Analysis(
    ["main.py"],
    pathex=[os.path.join(SPECPATH, "src")],
    binaries=[],
    datas=[(_TKDND_SRC, "tkinterdnd2/tkdnd"),
           (_LOGO_PNG, "assets"),
           (_LOGO_ICO, "assets")],
    hiddenimports=["tkinterdnd2"],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["fonttools"],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="CADBatchAssistant",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    icon=_LOGO_ICO,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
