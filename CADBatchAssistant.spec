# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec：「CAD批处理助手」统一应用（改字助手 + 填表助手）。

- 入口：main.py（Notebook 统一窗口）
- 产物：dist/CADBatchAssistant.exe（单文件、无控制台窗口；与 Release 资产同名，
  本地构建产物与 CI 一致）
- tkinterdnd2：手动收集其 tkdnd 扩展目录（hook-contrib 未覆盖），
  运行时从 os.path.dirname(__file__)/tkdnd/<platform> 加载
"""

import os

import tkinterdnd2

_TKDND_SRC = os.path.join(os.path.dirname(tkinterdnd2.__file__), "tkdnd")

a = Analysis(
    ["main.py"],
    pathex=[os.path.join(SPECPATH, "src")],
    binaries=[],
    datas=[(_TKDND_SRC, "tkinterdnd2/tkdnd")],
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
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
