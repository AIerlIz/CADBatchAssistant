# CAD批处理助手 (CADBatchAssistant)

统一的 CAD 图纸批处理桌面工具：一个窗口、两个功能页（tab 切换）。

| Tab | 功能 | 核心模块 |
|---|---|---|
| **改字助手** | 批量修改 DWG/DXF 图纸文字（TEXT / MTEXT / 块属性），支持正则查找替换 | `gui/gui.py` + `core/dxf_processor.py` |
| **填表助手** | 把数据表（.xlsx/.xls）内容按「图纸模板占位」填入 DWG/DXF 图纸标题栏 | `gui/gui_fill.py` + `core/pipeline.py` 等 |
| **设置** | ODA File Converter 路径、DWG 输出版本（全局共享，两个功能页共用） | `gui/settings.py` |

> 前身：CADBatchText（改字助手）与 CAD填表助手（CadFill）两个独立工具，现已整合为统一应用。

## 界面使用

启动后顶部为两个 tab，点按切换：

```bash
uv run python main.py
```

### 改字助手

1. **选择文件**：点击「选择文件...」或直接把 dwg/dxf 拖进窗口（可多次追加）
2. **配置规则**：在表格中编辑——双击单元格输入"查找（正则）"与"替换为"；单击底部「＋ 点击添加规则」新增行；选中行按 `Delete` 或右键删除
3. **设置输出**：默认输出到所选文件目录下的 `output`，可手动改或点「默认」还原
4. **选项**：DWG 输出版本（AutoCAD 2018/2013/…）、大小写敏感、dry-run 预览
5. **开始处理**：点击「开始处理」，进度条与日志实时显示每个文件的替换结果；完成后弹窗汇总

#### 正则规则说明

| 示例 | 说明 |
|---|---|
| `FABRICATION` | 普通文字直接写即可 |
| `REV\d+` | 匹配 `REV` 后跟数字（如 REV1、REV23） |
| 查找 `(FABRICATION)`，替换 `\1-NEW` | 捕获组反向引用 → `FABRICATION-NEW` |
| 替换 `C:\\Users\\X` | 路径等反斜杠需写成 `\\` |

> 替换文本中的无效反向引用（如 `\1` 无对应捕获组）或未转义反斜杠，提交时会被拦截并弹窗提示。

### 填表助手

1. **数据表**：选择项目数据表（.xlsx 或 .xls）
2. **图纸模板**：从模板库下拉框选择本项目图纸模板（可「上传」将 .dwg/.dxf 复制进软件目录下 `templates` 库）
3. **选择图纸文件...**：多选要处理的 DWG/DXF（可追加、右键删除、拖放）
4. **输出目录**：默认 = 第一个图纸所在目录下 `output`，可改
5. **开始处理**：进度条按阶段/图纸推进，完成后保持 100%

DWG 输入输出 DWG（需 ODA File Converter，自动探测）；DXF 输入输出 DXF（无需 ODA）。

#### 图纸模板占位格式（规范）

**模板 = 一张"未填图框 + 值格填占位文字"的样例图**，一个项目准备一份，全项目通用。
制作：打开任一张项目图纸（修改前/未填的图框），在**需要填入数据的值格**里，
填入**该数据表列名加方括号**的占位文字（如列名 `NPD (inch)` → 值格填 `[NPD (inch)]`），
另存为 `.dwg` 或 `.dxf` 均可（文件名随意）。

规则：

- **占位符文字 = 数据表该列的表头**（如列名 `NPD` → 填 `[NPD]`），**精确匹配、不做归一化**（大小写/空格/符号需与表头完全一致）。
- 占位所在位置即该字段的填入位置；每个要填的字段都放一个占位，缺哪个字段输出就漏填哪个。
- **数据表值原样填入**：不转分数、不加单位，表里是什么值就填什么值（如 `1.5` 填 `1.5`）。
- 数据表某字段值为空时，该字段占位符置空（输出为空白）。
- 模板与处理图纸必须**同一图框**（同布局/标签/图层/字体）；不同项目图框不同则各做一份。

## 环境要求

- Windows（Tk 图形界面）
- 处理 **DWG** 需要安装 [ODA File Converter](https://www.opendesign.com/guestfiles/oda_file_converter)（纯 DXF 场景不需要）
- 开发/构建需要 [uv](https://docs.astral.sh/uv/) + Python 3.12
- 依赖：`ezdxf`、`openpyxl`、`xlrd`、`tkinterdnd2`

## 快速开始

```bash
uv sync                # 安装依赖
uv run python main.py  # 启动统一窗口（两个 tab 切换）
```

## 打包 exe

运行：

```bash
uv run pyinstaller --noconfirm --clean CADBatchAssistant.spec
```

产物在 `dist\CAD批处理助手.exe`（单文件、免安装、无控制台窗口），包含「改字助手」与「填表助手」两个 tab。

- 填表助手的文件拖放依赖 `tkinterdnd2`：`CADBatchAssistant.spec` 已手动收集其 `tkdnd` 扩展目录（PyInstaller hook-contrib 未覆盖）
- DWG 处理仍需目标机器安装 [ODA File Converter](https://www.opendesign.com/guestfiles/oda_file_converter)（纯 DXF 流程不需要）

## 项目结构

```
main.py                   # 根薄入口：Notebook 窗口，装配两个面板 + 设置页
src/cadbatchassistant/
  common.py               # 两面板共享的公共组件：输出版本/配置/字体/去重/主题、
                          #   AsyncPanel 后台任务骨架（线程+队列+轮询）、ODA 行构建/探测/浏览助手、
                          #   通用控件构建（文件列表/日志面板/输出行/右键菜单）
  core/                   # 核心处理层
    dxf_processor.py      # DXF 文字查找替换核心（正则、编码、实体遍历）
    dwg_converter.py      # ODA File Converter 集成（探测、静默批量转换；两面板共用）
    pipeline.py           # 填表一键流程（xlsx + 图纸 → 模板推断规格 → 填表 → 输出）
    learn_spec.py         # 图纸模板占位扫描（[列名] 与数据表表头匹配）
    fill_dwg.py           # 按规格 + 数据表填充标题栏值格
    parse_xlsx.py         # 读取数据表 .xlsx/.xls → {图纸名: {列名: 值}}
  gui/                    # 界面层
    gui.py                # 「改字助手」面板（CadTextApp）
    gui_fill.py           # 「填表助手」面板（IsoFillApp，含模板库与拖放）
    settings.py           # 「设置」面板（SettingsPanel）：ODA 路径、DWG 输出版本，自动保存
```
