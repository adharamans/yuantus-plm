# 浩辰(GstarCAD)CAD 插件加载现状核查 + 加载操作清单

- **日期:** 2026-06-21
- **问题:** 我们的 CAD 插件能在浩辰(GstarCAD)中加载了吗?
- **结论(TL;DR):** **还不能算"已能加载"。** 浩辰的*探测、安装、后端受理、LISP 瘦壳*都已就位,但插件真正依赖的 **.NET 桥接 DLL 只编译了 AutoCAD 一版**(`#if AUTOCAD_HOST` + `Autodesk.AutoCAD.*`);**没有浩辰适配**,且"在 `gscad.exe` 里 NETLOAD"被代码与设计文档显式标记为 **deferred / 未验证**。在真机浩辰上按 runbook 走到"NETLOAD DLL"这一步,大概率命中 §5 blocker。

---

## 1. 分层现状(逐层 + 代码证据)

| 层 | 浩辰是否就绪 | 代码/文档证据 |
|---|---|---|
| 安装探测(Detector) | ✅ 已实现 | `clients/cad-desktop-helper/Detector/CadDetector.cs:590` `GstarCad()`:Vendor=`Gstarsoft`、注册表 `SOFTWARE\Gstarsoft\GstarCAD`、exe `GstarCAD.exe`;`:52` 纳入扫描 |
| Helper 后端受理 | ✅ 已实现 | `Helper/HelperRuntime.cs:885` 放行 `gscad.exe`(`C:\Program Files\Gstarsoft\GstarCAD*\gscad.exe`);`:2077` 接受 `cad_system=gstarcad` |
| LISP 瘦壳 | ✅ 已实现(共享源) | `Lisp/yuantus_cad_helper.lsp`:S10-R1 ZWCAD+GstarCAD 共享源,`(getvar "PROGRAM")` 命中 `gstarcad`/`gcad`;暴露 6 个命令 |
| 安装器 | ✅ 已实现 | `Installer/YuantusCadHelper.iss`:写 `%appdata%\YuantusPLM\cad-bridge\acad.lsp` 启动脚本(NETLOAD DLL + load LISP),候选启动位置含 AutoCAD/ZWCAD/GstarCAD |
| **.NET 桥接 DLL 宿主适配** | ❌ **缺失(仅 AutoCAD)** | `Bridge/Adapters/AutoCadHostAdapter.cs`:整段 `#if AUTOCAD_HOST`,引用 `Autodesk.AutoCAD.ApplicationServices/DatabaseServices/Runtime`、`[LispFunction]`。**没有** `GstarCadHostAdapter`/`GrxCAD` 版本(`find ... *HostAdapter*.cs` 只有 AutoCAD 一个) |

## 2. 为什么"还不能算能加载"

LISP 里的 `(yuantus-helper-call ...)` / `(yuantus-helper-upload ...)` 必须由桥接 DLL 注册,而该注册**只在 AutoCAD 版存在**:

- `AutoCadHostAdapter.cs:1-14` 注释明说:真实 AutoCAD 托管程序集(`acmgd.dll`/`accoremgd.dll`)在 CI 不可用,所以 **CI 默认是 SDK-free 构建,用 `AUTOCAD_HOST` 把宿主绑定代码整段排除** —— 也就是 CI 产出的 DLL 根本不是一个能在 CAD 里跑的插件。
- 同注释:"DLL 在 `acad.exe / ZWCAD.exe / gscad.exe` 里 NETLOAD……**deferred to native-CAD operational signoff(taskbook §3.K)**"。
- 设计文档 `docs/CAD_DESKTOP_HELPER_BRIDGE_DESIGN_R3_20260519.md`:
  - `:26` "国产 CAD(ZWCAD、GstarCAD)**完全空缺**";
  - `:51` 表行 "ZWCAD/GstarCAD LISP 瘦插件 | ⏳ R3 给出协议规范 + LISP 适配 DLL 设计(§5.7),**插件本身另起独立稿**";
  - `:259` 状态 `experimental` = "探测到但不保证写 DWG"。
- 验收 runbook `docs/CAD_HELPER_BRIDGE_NATIVE_CAD_OPERATIONAL_SIGNOFF_RUNBOOK_20260527.md` §6 汇总表里 **GstarCAD 2025 行仍为空**;§5 把 "DLL 无法 NETLOAD / 6 个命令不可见" 列为 **blocker**。

> 技术原因:AutoCAD 版 DLL 引用的是 `Autodesk.AutoCAD.*`(`acmgd.dll`)。浩辰加载的是自己的 `GcadMgd/GrxMgd` 程序集,没有 `Autodesk.AutoCAD.*`,因此把 AutoCAD 版 DLL 直接 NETLOAD 进 `gscad.exe`,类型解析会失败 → 加载报错。要可加载需针对浩辰 SDK 单独出一版适配。

## 3. 要让浩辰真正可加载,还差什么

1. **新增浩辰宿主适配**:照 `AutoCadHostAdapter.cs` 再写一个 `GstarCadHostAdapter`,改用浩辰 .NET API(`GrxCAD.*` / 浩辰的 `LispFunction` 等价物 + `GcadMgd/GrxMgd` 程序集引用),并加 `GSTARCAD_HOST` 编译分支与对应构建产物。(中望同理 `ZWCAD_HOST`。)
2. **(或)验证 AutoCAD 兼容层**:若用浩辰的 ARX/.NET 兼容模式直接加载 AutoCAD 版 DLL,必须真机验证 + 程序集重定向,属于未证实路径,不建议作为交付前提。
3. **真机加载验收**:按下面清单在真机浩辰 2025 跑通 6 个命令并归档证据,填 runbook §6 表,另开 evidence PR 宣告 signoff。
4. 说明:即便加载成功,**当前浩辰上是只读/显示**(diff 不回写 DWG;回写仅 AutoCAD 有)。

## 4. 加载 / 验收操作清单(摘自 runbook §3–§5)

> ⚠️ 以现状执行,第 4 步(NETLOAD)很可能就是 blocker —— 这正是用来暴露"浩辰适配未完成"的检查点。

**预检(每台机记一次,存 `preflight-gstarcad2025.txt`):**
1. 记录 Windows 版本、浩辰版本、PLM 租户 + 服务端 URL。
2. 记录 helper 构建/哈希、`YuantusCadHelperBridge.dll` 路径、`yuantus_cad_helper.lsp` 路径。
3. 确认 helper 在运行且 PLM 会话有效(helper `/session/status`)。
4. 在浩辰命令行 `NETLOAD` 加载 `YuantusCadHelperBridge.dll` —— **应无加载错误**。
5. `(load "yuantus_cad_helper.lsp")` —— 应干净加载。
6. 加载提示应列出全部 6 个命令:`YUANTUS_DIFF_PREVIEW, YUANTUS_CHECKOUT, YUANTUS_UNDO_CHECKOUT, YUANTUS_STATUS, YUANTUS_CHECKIN, YUANTUS_BOM_IMPORT`。
7. 用 ASCII 文件名的测试图(如 `native_signoff.dwg`),记录 `DWGPREFIX + DWGNAME` 与文件大小。

**命令逐条验证(抓命令行 transcript + 截图):**
- `YUANTUS_DIFF_PREVIEW` → 显示 helper `data` JSON,**不写 DWG**,产生 `/audit/apply-result`(`not-applied-display-only`)。
- `YUANTUS_CHECKOUT` / `STATUS` / `UNDO_CHECKOUT` → 调对应 `/document/*`,PLM 端锁状态相应变化(这几个**不产生** apply-result,证据看 PLM 后端效果 + transcript)。
- `YUANTUS_CHECKIN`(正)→ 已保存图(`DBMOD=0`)上传;服务端文件名/大小与当前图一致。(负)→ 空 item_id 取消、脏图提示先保存,均**不上传**。
- `YUANTUS_BOM_IMPORT`(正)→ 上传返回 `file_id` + `cad_bom` 作业;(负)脏图提示先保存。

**blocker(出现即不能宣告 signoff):** DLL 无法 NETLOAD;`.lsp` 加载失败或 6 命令不可见;脏图(`DBMOD≠0`)仍上传;服务端文件元数据与当前图矛盾。

**证据归档:** `%APPDATA%\YuantusPLM\acceptance-evidence\native-cad-last-mile\20260527\gstarcad2025\`,文件名 `row<N>-<command>-gstarcad.<ext>`。

## 5. 参考
- 加载/验收 runbook:`docs/CAD_HELPER_BRIDGE_NATIVE_CAD_OPERATIONAL_SIGNOFF_RUNBOOK_20260527.md`
- 设计:`docs/CAD_DESKTOP_HELPER_BRIDGE_DESIGN_R3_20260519.md`(§5.7 浩辰 LISP 适配 DLL 设计、状态表)
- 关键代码:`Bridge/Adapters/AutoCadHostAdapter.cs`、`Detector/CadDetector.cs:590`、`Helper/HelperRuntime.cs:885`、`Lisp/yuantus_cad_helper.lsp`、`Installer/YuantusCadHelper.iss`
