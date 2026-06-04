# AutoCAD PLM 物料同步指南

## 功能范围

本插件在原有 CAD 查重功能上增加 Yuantus PLM 物料同步命令：

- 从 PLM 获取物料 profile。
- 根据 profile 在 AutoCAD 命令行输入字段，调用 PLM 合成规格。
- 将 PLM 返回的 `cad_fields` 写回标题栏块属性或明细表相邻单元格。
- 从当前图纸标题栏/明细表提取字段并同步到 PLM。
- 按 PLM Item ID 拉取字段，调用 `/diff/preview` 做本地差异预览，用户确认后只写回 `write_cad_fields`。

服务端依赖 Yuantus 插件：

- `/api/v1/plugins/cad-material-sync/profiles`
- `/api/v1/plugins/cad-material-sync/compose`
- `/api/v1/plugins/cad-material-sync/diff/preview`
- `/api/v1/plugins/cad-material-sync/sync/inbound`
- `/api/v1/plugins/cad-material-sync/sync/outbound`

## 适配架构

- `MaterialSyncApiClient` 负责和 Yuantus `cad-material-sync` 插件通信。
- `ICadMaterialFieldAdapter<TCadDocument>` 定义 CAD 字段抽取/回填接口，后续 SolidWorks 或其他 CAD 客户端可以复用同一协议。
- `CadMaterialFieldMapper` 承载字段别名、规范化、`字段=值` 和 `字段 | 值` 表格规则，不依赖 AutoCAD SDK，可在 macOS 用 fixture 验证。
- `CadMaterialFieldService` 是 AutoCAD 适配实现，负责把真实 DWG 的块属性和 `Table` 转给 mapper。
- `verify_material_sync_e2e.py` 会把 mock drawing fixture 抽取出的字段提交给当前 Yuantus 插件，再把返回的 `cad_fields` 回填到 fixture，验证客户端和服务端契约。
- `verify_material_sync_db_e2e.py` 会在 SQLite 中创建最小 PLM DB，验证 dry-run、真实 Item 创建、真实 Item 更新和 outbound CAD 字段包。

## 配置

在 AutoCAD 命令行执行：

```text
DEDUPCONFIG
```

服务器设置页需要填写：

- `服务器地址`：Yuantus PLM 服务地址，例如 `http://127.0.0.1:7910`
- `API密钥`：如果 PLM 启用认证，填写 Bearer token
- `租户 ID`：默认 `tenant-1`
- `组织 ID`：默认 `org-1`
- `物料 profile`：默认 `sheet`
- `物料回写默认仅预演`：建议启用，避免误写 PLM

## 命令

```text
PLMMATPROFILES
```

列出 PLM 侧当前可用物料 profile 和字段。

```text
PLMMATCOMPOSE
```

按 profile 逐项输入字段，PLM 合成规格后回填当前图纸。适合用户在插件中输入长宽厚、材料等字段，然后写回明细栏。

```text
PLMMATPUSH
```

从当前图纸块属性和表格中提取字段，调用 PLM 入站同步。默认 dry-run；需要真实写入时按提示选择非预演。已有 PLM 字段默认不覆盖，除非按提示明确允许 overwrite。

```text
PLMMATPULL
```

输入 PLM Item ID，从 PLM 拉取物料目标字段并先展示差异预览窗口。窗口列出 CAD 字段、当前值、目标值和状态；只有用户点击确认后，插件才会把 `write_cad_fields` 写回当前图纸。

```text
PLMMATASSIST
```

物料助手：读取当前图纸字段，调用 `/material/assistant/resolve`（经 Helper Bridge）解析并**只展示**精确匹配、相似候选、草稿建议；用户取消则零写入。仅在用户显式确认（默认 No）后才调用 `/material/assistant/create` 创建 Draft 物料，创建成功只展示 `item_id`/`item_number`/`state`/`current_state`/`draft_check`。本期**不回写 DWG**，也不提供"绑定已有物料"动作（assistant/create 不返回 CAD field package，绑定/回写待后续 contract）。

## CAD 字段匹配

当前客户端会读取和写入：

- 块属性：按 Attribute Tag 匹配。
- 表格：按 `字段名=值` 或 `字段名 | 值` 相邻单元格匹配。
- 范围：扫描 ModelSpace 和全部 Layout/PaperSpace 的块表记录，避免只处理当前布局。

内置别名包括：

- `图号`、`drawing_no`、`part_number` -> `item_number`
- `名称`、`品名`、`description` -> `name`
- `材料`、`材质` -> `material`
- `规格`、`规格型号`、`物料规格` -> `specification`
- `长`、`宽`、`厚`、`外径`、`壁厚`、`直径`、`毛坯尺寸`、`热处理`

## 验证建议

### 本地静态验证

在仓库根目录执行：

```bash
python3 clients/autocad-material-sync/verify_material_sync_static.py
python3 clients/autocad-material-sync/verify_material_sync_fixture.py
python3 clients/autocad-material-sync/verify_material_sync_e2e.py
python3 clients/autocad-material-sync/verify_material_sync_db_e2e.py
xmllint --noout clients/autocad-material-sync/CADDedupPlugin/CADDedupPlugin.csproj clients/autocad-material-sync/CADDedupPlugin/PackageContents.xml
```

脚本默认会从 `clients/autocad-material-sync` 向上识别当前 Yuantus 仓库。若在其他目录单独运行客户端副本，可显式指定：

```bash
YUANTUS_ROOT=/path/to/Yuantus python3 clients/autocad-material-sync/verify_material_sync_e2e.py
YUANTUS_ROOT=/path/to/Yuantus python3 clients/autocad-material-sync/verify_material_sync_db_e2e.py
```

### Windows + AutoCAD 2018 验证

1. 启动 Yuantus，并启用 `yuantus-cad-material-sync` 插件。
2. 在 Windows + AutoCAD 2018 环境编译并加载插件 DLL。
3. 执行 `PLMMATPROFILES`，确认能列出 `sheet/tube/bar/forging`。
4. 打开带标题栏块属性或明细表的 DWG。
5. 执行 `PLMMATCOMPOSE`，选择 `sheet`，输入材料、长、宽、厚，确认 `规格` 字段写回为 `长*宽*厚`。
6. 执行 `PLMMATPUSH`，先保持 dry-run，确认 PLM 返回 `created/updated/conflict` 结果。
7. 对测试物料执行真实写入，再执行 `PLMMATPULL`，在差异预览窗口确认后回填另一张图纸。

默认构建基线为 AutoCAD 2018：

```batch
build_simple.bat
```

显式切换高版本构建：

```batch
set AUTOCAD_VERSION=2024
set AUTOCAD_INSTALL_DIR=C:\Program Files\Autodesk\AutoCAD 2024
build_simple.bat
```

2018 支持必须以 `ACADVER` 返回 `R22.0` 的真实 AutoCAD 2018 环境为准。macOS fixture 和静态验证不能替代 DLL 加载、命令注册和 DWG 写回 smoke。

完整 Windows 验收流程见 `WINDOWS_AUTOCAD2018_VALIDATION_GUIDE.md`。现场编译前建议先运行：

```powershell
powershell -ExecutionPolicy Bypass -File .\verify_autocad2018_preflight.ps1
```

## 安全边界

- 默认 dry-run 是保护策略，真实回写 PLM 需要用户确认。
- `overwrite` 默认为 false，已有 PLM 字段冲突会返回给用户，不会静默覆盖。
- 服务端仍以结构化源字段为事实源，`specification` 是派生字段。
