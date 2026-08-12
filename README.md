# CAD批处理助手 (CADBatchAssistant)

统一的 CAD 图纸批处理桌面工具：一个窗口、三个功能页 + 设置页（tab 切换），
支持 DWG / DXF 图纸的**批量改字**、**数据表填图**与**图纸目录生成**。

| Tab | 功能 | 核心模块 |
|---|---|---|
| **改字助手** | 批量修改 DWG/DXF 图纸文字（TEXT/MTEXT/块属性），支持正则查找替换 | `gui/gui_text.py` + `core/text_replace.py` |
| **填表助手** | 把数据表（.xlsx/.xls）按「图纸模板占位」填入图纸标题栏 | `gui/gui_fill.py` + `core/fill_pipeline.py` |
| **目录助手** | 按「图纸模板」从一批图纸取值，生成图纸目录 Excel | `gui/gui_catalog.py` + `core/catalog_pipeline.py` |
| **设置** | ODA File Converter 路径、DWG 输出版本、软件更新（全局共享） | `gui/settings.py` |

> 前身：CADBatchText、CadFill、CADCatalogAssistant 三个独立工具，现已整合为统一应用。

## 界面使用

```bash
uv run python main.py
```

### 改字助手

选择 DWG/DXF（可拖放追加）→ 编辑查找/替换规则（双击单元格编辑、底部「＋」添加、Delete/右键删除）→ 设置输出目录 → 开始处理。
默认「普通文本」模式按字面匹配；勾选「正则模式」后查找按正则解释、替换支持 `\1` 反向引用。

### 填表助手

1. **数据表格**：选择 .xlsx/.xls，可选「工作表格」（sheet）与「匹配列」（图纸名列，默认第一列）
2. **图纸模板**：从模板库下拉选择（可「上传」到 `templates/fill`），模板为「未填图框 + 值格填 `[列名]` 占位」的样例图，占位符列名与数据表表头**精确匹配**
3. 选择图纸（可拖放）→ 设置输出目录 → 开始处理

**取值规则**：每个占位符 `[列名]` 只从数据表对应列取值，其他列不参与；列缺失/值为空时该字段置空。

### 目录助手

1. **图纸模板**：在取值位置放 `[字段名]` 文字（如 `[图号]`），可放多个同名候选位；用小矩形圈住区域则按区域内全部文字取值。上传到模板库下拉（`templates/catalog`）
2. **表格模板（必填）**：Excel 表头列名 = 模板字段名 +「页码」，程序自动定位 sheet 与表头行；多个 sheet 并列时弹窗选择。可用 `write_style_template` 生成参考模板
3. 选择图纸 → 设置输出目录 → 开始处理

输出：每图纸一个条目，列 = 占位符字段名 + 页码；无值字段填 `NA`；单值字段跨行合并；页码每文件一页；图号只从图纸中提取，取不到时填 `NA`（不做文件名兜底）。

**配置规则**（软件目录 `config.json` 的 `rules` 段，缺省用默认）：

| 键 | 默认值 | 说明 |
|---|---|---|
| `figure_field` | `图号` | 图号字段名（用于识别图号列，取不到填 `NA`，不做文件名兜底） |
| `point_tolerance` | `5` | 单点锚点取值坐标容差 |
| `data_rows_per_page` | `50` | 目录每页数据行数 |
| `cover_pages` | `1` | 封皮页数 |

## 模板库存放约定

| 目录 | 用途 | 使用功能页 |
|---|---|---|
| `templates/fill/` | 图纸模板（未填图框 + 值格 `[列名]` 占位） | 填表助手 |
| `templates/catalog/` | 图纸模板（`[字段名]` 取值位置） | 目录助手 |

两个模板库各自独立管理（上传/删除/拖放互不干扰），目录在软件目录下自动创建。

## 环境要求

- Windows（Tk 图形界面）
- 处理 DWG 需安装 [ODA File Converter](https://www.opendesign.com/guestfiles/oda_file_converter)（纯 DXF 场景不需要）
- 开发/构建：Python 3.12 + [uv](https://docs.astral.sh/uv/)
- 依赖：`ezdxf`、`openpyxl`、`xlrd`、`tkinterdnd2`

## 打包 exe

```bash
uv run python scripts/inject_version.py   # 注入版本号（本地取 pyproject.toml）
uv run pyinstaller --noconfirm --clean CADBatchAssistant.spec
```

产物 `dist\CADBatchAssistant.exe`（单文件、无控制台窗口），DWG 处理仍需目标机安装 ODA File Converter。

## 软件更新

打包版启动后静默检查 GitHub Release，发现新版本可应用内下载并替换重启；支持下载镜像与失败重试。
发布：推送 `v*` tag（如 `v2.0.0`）→ CI 构建并发布 Release（资产名 `CADBatchAssistant.exe`）。

## 首次使用引导

首次启动（或删除全局配置文件后）会弹出欢迎引导窗口，简介三个功能页与 DWG 处理提示；
关闭引导（「开始使用」/「稍后再说」/窗口 X 均可）即在全局配置写入 `welcome_seen`
标记（`%APPDATA%\CADBatchAssistant\config.json`），之后不再自动弹出。
需要回看时，在「设置」页点击「重新显示使用引导」。

## 诊断模式

```bash
CADBatchAssistant.exe --selftest <图纸模板DWG> <图纸文件...>
```

不启动界面跑完整目录流程，日志写入 `selftest_log.txt`，供定位打包版问题。

## 项目结构

```
main.py                    # 入口：Notebook 窗口 + --selftest
src/cadbatchassistant/
  common.py                # 共享组件：配置、软件目录/模板库函数、控件、AsyncPanel
  core/
    text_replace.py        # 改字：DXF 文字查找替换
    dwg_converter.py       # ODA 集成（三功能共用）
    fill_pipeline.py       # 填表：一键流程
    fill_learn_spec.py     # 填表：模板占位扫描
    fill_dwg.py            # 填表：按规格填充
    fill_parse_xlsx.py     # 填表：读取数据表
    catalog_pipeline.py    # 目录：一键流程
    catalog_template_reader.py  # 目录：模板解析
    catalog_reader.py      # 目录：按锚点取值
    catalog_builder.py     # 目录：目录数据构建
    catalog_excel_writer.py     # 目录：Excel 输出
    updater.py             # 在线更新
  gui/
    gui_text.py            # 改字助手面板
    gui_fill.py            # 填表助手面板
    gui_catalog.py         # 目录助手面板
    settings.py            # 设置面板
    updater_dialog.py      # 更新对话框
scripts/
  inject_version.py        # 打包版本注入
  verify_end_to_end.py     # 目录助手端到端验证
tests/                     # pytest 单测
```

## 验证

```bash
uv run pytest
uv run python scripts/verify_end_to_end.py
```
