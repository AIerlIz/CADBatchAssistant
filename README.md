# CAD批处理助手 (CADBatchAssistant)

统一的 CAD 图纸批处理桌面工具：一个窗口、三个功能页 + 设置页（tab 切换），
支持 DWG / DXF 图纸的**批量改字**、**数据表填图**与**图纸目录生成**。

| Tab | 功能 | 核心模块 |
|---|---|---|
| **改字助手** | 批量修改 DWG/DXF 图纸文字（TEXT/MTEXT/块属性），支持正则查找替换 | `gui/panels/gui_text.py` + `core/common/text_replace.py` |
| **填表助手** | 把数据表（.xlsx/.xls）按「图纸模板占位」填入图纸标题栏 | `gui/panels/gui_fill.py` + `core/fill/fill_pipeline.py` |
| **目录助手** | 按「图纸模板」从一批图纸取值，生成图纸目录 Excel | `gui/panels/gui_catalog.py` + `core/catalog/catalog_pipeline.py` |
| **设置** | ODA File Converter 路径、DWG 输出版本、软件更新（全局共享） | `gui/dialogs/settings.py` |

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
2. **图纸模板**：从模板库下拉选择（可「上传」到 `templates/fill`），模板为「未填图框 + 值格填 `[列名]` 占位」的样例图，占位符列名与数据表表头**精确匹配**。**上传时自动扫描全部 `[列名]` 占位符并存入伴生 JSON**（`templates/fill/<模板名>.json`），运行只读该 JSON、不再重复解析模板；**模板无占位符或解析失败（如 DWG 缺少 ODA）会被拒绝上传**
3. 选择图纸（可拖放）→ 设置输出目录 → 开始处理

**取值规则**：每个占位符 `[列名]` 只从数据表对应列取值，其他列不参与；列缺失/值为空时该字段置空；「占位符与本次数据表列零匹配」时警告并按无字段处理（输出原图）。

### 目录助手

1. **图纸模板**：在取值位置放 `[字段名]` 文字（如 `[图号]`），可放多个同名候选位；用小矩形圈住区域则按区域内全部文字取值。取值区域 = 占位符文字在模板中的覆盖范围（包围盒），**把占位符文字调大即可扩大取值区域**，不再依赖坐标容差。上传到模板库下拉（`templates/catalog`）；**上传时自动提取占位符/区域并存入伴生 JSON**（`templates/catalog/<模板名>.json`），运行只读该 JSON、不再把模板转 DXF 解析；**模板无 `[字段名]` 占位符或解析失败会被拒绝上传**
2. **表格模板（必填）**：Excel 表头列名 = 模板字段名 +「页码」，程序自动定位 sheet 与表头行；多个 sheet 并列时弹窗选择。可用 `write_style_template` 生成参考模板
3. 选择图纸 → 设置输出目录 → 开始处理

输出：每图纸一个条目，列 = 占位符字段名 + 页码；无值字段填 `NA`；单值字段跨行合并；页码每文件一页；图号只从图纸中提取，取不到时填 `NA`（不做文件名兜底）。

**配置规则**（软件目录 `config.json` 的 `rules` 段，缺省用默认）：

| 键 | 默认值 | 说明 |
|---|---|---|
| `data_rows_per_page` | `50` | 目录每页数据行数 |
| `cover_pages` | `1` | 封皮页数 |

## 模板库存放约定

| 目录 | 用途 | 使用功能页 |
|---|---|---|
| `templates/fill/` | 图纸模板占位配置（未填图框 + 值格 `[列名]` 占位） | 填表助手 |
| `templates/catalog/` | 图纸模板占位配置（`[字段名]` 取值位置） | 目录助手 |

两个模板库各自独立管理（上传/删除/拖放互不干扰），目录在软件目录下自动创建。

**上传时只把解析出的占位配置 JSON 存入模板库**（`<模板名>.json`，如 `图框.dwg.json`），**不保存原始 dwg/dxf 文件**：填表助手存全部 `[列名]` 占位符规格（位置/字高/样式/对齐/实体样式描述），目录助手存 `[字段名]` 锚点（字段名 + 取值区域坐标）。运行流程**只读该 JSON**，不再重复把模板转 DXF 解析；删除模板时 JSON 一并删除。

> 模板库只识别占位配置 JSON；JSON 可手工编辑（如直接改字段名、取值区域坐标、字高样式），改坏后删除模板重新上传即可恢复；命令行/`--selftest` 等直接传模板路径的场景在 JSON 缺失时会现场解析（行为不变）。

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

## 诊断模式

```bash
CADBatchAssistant.exe --selftest <图纸模板DWG> <图纸文件...>
```

不启动界面跑完整目录流程，日志写入 `selftest_log.txt`，供定位打包版问题。

## 项目结构

```
main.py                    # 入口：Notebook 窗口 + --selftest
src/cadbatchassistant/
  core/
    common/                # 跨功能域共享
      app_config.py        # 全局配置：JSON 读写、软件目录、目录助手规则、输出版本
      templates.py         # 模板库纯文件操作（枚举/删除，只存占位配置 JSON，无 GUI 依赖）
      filetypes.py         # 共享文件扩展名常量（CAD_SUFFIXES / XLSX_SUFFIXES）
      text_replace.py      # 改字：DXF 文字查找替换
      parallel.py          # 并行执行器（串行/进程/线程可切换）
      input_files.py       # 输入文件公共工具（重名检测 + 复制暂存）
      template_meta.py     # 占位符 meta 读写（填表/目录模板共用）
      dwg_workflow.py      # DWG 批处理工作流（统一成 DXF 批 / 处理后写回）
      app_log.py           # 统一日志
    dwg_converter/         # 转换引擎抽象：Converter 接口 + ODA 实现（三功能共用）
    fill/                  # 填表
      fill_pipeline.py     # 一键流程
      fill_learn_spec.py   # 模板占位扫描
      fill_dwg.py          # 按规格填充
      fill_parse_xlsx.py   # 读取数据表
    catalog/               # 目录
      catalog_pipeline.py  # 一键流程
      catalog_template_reader.py  # 模板解析
      catalog_reader.py    # 按锚点取值
      catalog_builder.py   # 目录数据构建
      catalog_excel_writer.py     # Excel 输出
    updater/               # 在线更新（版本/检查/下载/替换）
  gui/
    components/            # 通用组件
      async_panel.py       # 后台任务骨架：后台线程 + 消息队列 + after 轮询
      tk_util.py           # GUI 通用工具：字体/主题/居中/去重/拖放解析
      tk_widgets.py        # 通用控件构建 + ODA 助手 + 模板库弹窗包装
    mixins/                # 面板共享 Mixin
      gui_shared.py        # FilesPanel/TemplateLibrary/PanelLayout/RunStart Mixin
    panels/                # 三个功能面板
      gui_text.py          # 改字助手面板
      gui_fill.py          # 填表助手面板
      gui_catalog.py       # 目录助手面板
    dialogs/               # 对话框
      settings.py          # 设置面板
      updater_dialog.py    # 更新对话框
scripts/
  inject_version.py        # 打包版本注入
  verify_end_to_end.py     # 目录助手端到端验证
tests/                     # pytest 单测（core 纯逻辑 / GUI 冒烟 / updater 等）
```

## 验证

```bash
uv run pytest
uv run python scripts/verify_end_to_end.py
```
