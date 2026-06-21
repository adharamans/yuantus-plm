# 原生 CAD 桥接插件 —— 真机编译 + 加载操作手册(AutoCAD / 浩辰 GstarCAD / 中望 ZWCAD)

- **日期:** 2026-06-21
- **适用:** `clients/cad-desktop-helper` 的 NETLOAD 桥(`YuantusCadHelperBridge.dll`)+ 共享 LISP 瘦壳(`yuantus_cad_helper.lsp`),三家宿主:AutoCAD、浩辰(GstarCAD)、中望(ZWCAD)。
- **目的:** 把"在有 CAD + 对应 .NET SDK 的真机上,从源码编译出宿主 DLL 并加载到 CAD 里"的步骤统一成一份可执行清单。
- **前置事实:** 桥的宿主适配是 **源码 + `#if <HOST>` 保护**,签入的 `csproj` **不带任何宿主程序集引用**(AutoCAD 也一样),所以宿主 DLL 必须在真机上自行补引用并定义编译符号后构建。本仓库 CI 只做 SDK-free 构建 + 静态校验;真机 NETLOAD 验收是**独立的、尚未完成的**一步(deferred)。

---

## 0. 总览(三家共用的 5 步)

1. **装好** 目标 CAD + 它的 .NET 开发程序集(见 §1 表)。
2. **编译**:给 `YuantusCadHelperBridge.csproj` 定义该宿主的编译符号 + 引用该家的托管程序集,产出 `YuantusCadHelperBridge.dll`(.NET Framework v4.6)。
3. **部署**:把 DLL、`yuantus_cad_helper.lsp`、helper 程序(`yuantus-cad-helper.exe`)放到一处;helper 先跑起来且 PLM 会话有效。
4. **加载**:在 CAD 命令行 `NETLOAD` 该 DLL,再 `(load "yuantus_cad_helper.lsp")`,确认 6 个命令可见。
5. **验收**:逐条跑 6 命令,按签收 runbook 归档证据,填 §6 汇总表。

> 6 个命令:`YUANTUS_DIFF_PREVIEW`、`YUANTUS_CHECKOUT`、`YUANTUS_UNDO_CHECKOUT`、`YUANTUS_STATUS`、`YUANTUS_CHECKIN`、`YUANTUS_BOM_IMPORT`。
> 在**国产 CAD(浩辰/中望)上是只读/显示**:diff 只显示不回写 DWG;回写仅 AutoCAD。

## 1. 编译(按宿主)

| 宿主 | 编译符号 | 托管程序集(引用,Copy Local=False) | 程序集位置(典型) | 适配源码 / 命名空间 |
|---|---|---|---|---|
| AutoCAD | `AUTOCAD_HOST` | `acmgd.dll`, `acdbmgd.dll`, `accoremgd.dll` | `C:\Program Files\Autodesk\AutoCAD <年>\` | `AutoCadHostAdapter.cs` / `Autodesk.AutoCAD.*` |
| 浩辰 GstarCAD | `GSTARCAD_HOST` | `GcMgd.dll`, `GcDbMgd.dll`, `GcCoreMgd.dll` | `<GstarCAD>\arx\inc\` | `GstarCadHostAdapter.cs` / `Gssoft.Gscad.*` |
| 中望 ZWCAD | `ZWCAD_HOST` | `ZwManaged.dll`, `ZwDatabaseMgd.dll` | ZWCAD 安装目录 | `ZwCadHostAdapter.cs` / `ZwSoft.ZwCAD.*` |

**做法 A(推荐,临时 props 注入引用):** 新建一个 `host.props`,按目标宿主填,然后构建时引用它。例如浩辰:

```xml
<!-- host.props (GstarCAD) -->
<Project>
  <PropertyGroup>
    <DefineConstants>$(DefineConstants);GSTARCAD_HOST</DefineConstants>
    <GcadInc>C:\Program Files\Gstarsoft\GstarCAD 2025\arx\inc</GcadInc>
  </PropertyGroup>
  <ItemGroup>
    <Reference Include="GcMgd"><HintPath>$(GcadInc)\GcMgd.dll</HintPath><Private>false</Private></Reference>
    <Reference Include="GcDbMgd"><HintPath>$(GcadInc)\GcDbMgd.dll</HintPath><Private>false</Private></Reference>
    <Reference Include="GcCoreMgd"><HintPath>$(GcadInc)\GcCoreMgd.dll</HintPath><Private>false</Private></Reference>
  </ItemGroup>
</Project>
```

```
msbuild clients/cad-desktop-helper/Bridge/YuantusCadHelperBridge.csproj ^
  /p:Configuration=Release /p:ForceImportBeforeCsprojDotNet=host.props
```

中望把符号换成 `ZWCAD_HOST`、引用 `ZwManaged.dll` + `ZwDatabaseMgd.dll`;AutoCAD 换成 `AUTOCAD_HOST`、引用 `acmgd/acdbmgd/accoremgd`。

**做法 B(命令行直接定义符号 + 手工加引用):** `-p:DefineConstants=GSTARCAD_HOST` 并在 csproj 临时加上对应 `<Reference>`。注意:**不要把宿主引用提交进签入的 csproj**(会破坏 CI 的 SDK-free 构建)。

> ⚠️ 各家程序集的**确切文件名/版本/路径以本机安装的 SDK 为准**(尤其浩辰 `Gc*` 与中望 `Zw*` 不同版本可能略有差异)。首次构建若报缺程序集,按 IDE 提示在 SDK 目录里定位同名 DLL。

## 2. 部署

把以下放到同一可写目录(例如 helper bundle 目录),并保证 helper 已运行:
- `YuantusCadHelperBridge.dll`(刚编出的宿主版)
- `clients/cad-desktop-helper/Lisp/yuantus_cad_helper.lsp`
- `yuantus-cad-helper.exe`(helper;`%APPDATA%\YuantusPLM\helper-session-*.json` 提供端口,DPAPI 提供 `local-helper-token`)

安装器 `clients/cad-desktop-helper/Installer/YuantusCadHelper.iss` 会写一份启动 `acad.lsp`(`%APPDATA%\YuantusPLM\cad-bridge\acad.lsp`)做 NETLOAD;三家都认这种 per-user 搜索路径启动文件。

## 3. 加载(签收 runbook §3 预检,每台机记一次)

记录到 `preflight-<host>.txt`(`<host>` ∈ `autocad2018|autocad2024|gstarcad2025|zwcad2025`):
1. Windows 版本;CAD 名+版本;PLM 租户 + 服务端 URL。
2. helper 构建/哈希;`YuantusCadHelperBridge.dll` 路径;`yuantus_cad_helper.lsp` 路径。
3. helper 在跑且 PLM 会话有效(helper `/session/status`)。
4. CAD 命令行 `NETLOAD` 该 DLL —— **应无加载错误**。
5. `(load "yuantus_cad_helper.lsp")` —— 应干净加载。
6. 加载提示应列出全部 **6 个命令**。
7. 用 ASCII 文件名的测试图(如 `native_signoff.dwg`),记下 `DWGPREFIX + DWGNAME` 与大小。

任一步失败即停,记为 §5 blocker。

## 4. 验收(签收 runbook §4/§5/§2)

逐条跑并抓 transcript + 截图:
- `YUANTUS_DIFF_PREVIEW` → 显示 helper `data` JSON,**不写 DWG**,有 `/audit/apply-result`(`outcome=not-applied-display-only`)。
- `YUANTUS_CHECKOUT` / `STATUS` / `UNDO_CHECKOUT` → 调 `/document/*`,PLM 端锁状态相应变化(这几个**不产生** apply-result;看 PLM 后端效果 + transcript)。
- `YUANTUS_CHECKIN`(正)已保存图上传,服务端文件名/大小与当前图一致;(负)空 item_id 取消、脏图(`DBMOD≠0`)提示先存,均**不上传**。
- `YUANTUS_BOM_IMPORT`(正)上传返回 `file_id` + `cad_bom` 作业;(负)脏图提示先存。

**blocker(出现即不能宣告 signoff):** DLL 无法 NETLOAD;`.lsp` 加载失败或 6 命令不可见;脏图仍上传;服务端文件元数据与当前图矛盾。
**证据归档:** `%APPDATA%\YuantusPLM\acceptance-evidence\native-cad-last-mile\<日期>\<host>\`,文件名 `row<N>-<command>-<host>.<ext>`。

## 5. 各家探测 / 进程 / 注册表对照

| 宿主 | 进程映像 | 注册表根 | 代码探测 |
|---|---|---|---|
| AutoCAD | `acad.exe` | `HKLM\SOFTWARE\Autodesk\AutoCAD\<ver>` | `Detector/CadDetector.cs` |
| 浩辰 GstarCAD | `GstarCAD.exe` / `gscad.exe` | `HKLM\SOFTWARE\Gstarsoft\GstarCAD\<年>` | `CadDetector.cs:590` `GstarCad()` |
| 中望 ZWCAD | `ZWCAD.exe` | `HKLM\SOFTWARE\ZWSOFT\ZWCAD\<年>` | `CadDetector.cs:582` `ZWSOFT` |

LISP 瘦壳 `yuantus_cad_helper.lsp` 用 `(getvar "PROGRAM")` 自动识别 `zwcad`/`gstarcad`/`gcad`,无需为每家改 LISP。

## 6. 重要限制(诚实说明)

- **签入仓库的状态 = 源码骨架**:三家适配都是 `#if <HOST>` 源码,csproj 不含宿主引用;真机 DLL 必须按 §1 自行构建。
- **国产 CAD 只读**:浩辰/中望上 diff 不回写 DWG(R3 设计 `:724`)。
- **真机签收 deferred**:本仓库无任一 CAD 的 .NET SDK、无真机,故只过了静态校验(`verify_bridge_static.py` 13/13、`verify_lisp_shell_static.py` 29/29);"已能加载"需在真机按本手册跑通并归档。
- 各家程序集确切名称/版本以**本机 SDK** 为准。

## 参考
- `docs/CAD_HELPER_BRIDGE_NATIVE_CAD_OPERATIONAL_SIGNOFF_RUNBOOK_20260527.md`(官方真机签收 runbook,§6 汇总表 AutoCAD/ZWCAD/GstarCAD 行)
- `docs/DEV_AND_VERIFICATION_CAD_HELPER_BRIDGE_GSTARCAD_HOST_ADAPTER_R1_20260621.md`
- `docs/DEV_AND_VERIFICATION_CAD_HELPER_BRIDGE_ZWCAD_HOST_ADAPTER_R1_20260621.md`
- `docs/CAD_DESKTOP_HELPER_BRIDGE_DESIGN_R3_20260519.md` §5.7(LISP 桥协议)
- 适配源码:`clients/cad-desktop-helper/Bridge/Adapters/{AutoCad,GstarCad,ZwCad}HostAdapter.cs`
