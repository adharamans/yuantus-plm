# CAD 物料助手 v1 — Phase 3 任务书：Helper Bridge 转发 + AutoCAD PLMMATASSIST

- Date: 2026-06-04
- Status: Task (doc-only; 实现需单独开工授权)
- Parent plan: `docs/DEVELOPMENT_CAD_MATERIAL_ASSISTANT_V1_PLAN_20260603.md` (#701)
- Predecessors: Phase 1 #702/#704，Phase 2 #706/#707（assistant `resolve`/`create` 已在 PLM 主线）
- Phase: 3 / N

## 1. 目标

把 Phase 2 的两条 PLM 路由（`/plugins/cad-material-sync/assistant/resolve`、`/assistant/create`）经 **CAD Helper Bridge** 转发，并加 AutoCAD 命令 **`PLMMATASSIST`**，让设计端在图纸里直接用物料助手。沿用既有 S8 material-sync 转发模式，不另起通道。

## 2. 关键约束：CI-可测 vs 延后 manual（最重要）

CI（`.github/workflows/cad-helper-shared-dotnet.yml`）只跑 **SDK-free** 工程，**不构建 `CADDedupPlugin.csproj`**（需 AutoCAD 托管程序集，仅 Windows 操作签核可得）。因此 Phase 3 必须分两层：

- **CI-可测（SDK-free，本阶段必达 + 测试）**：
  - `MaterialSyncApiClient.ResolveAsync/CreateAsync`（helper-forwarded），链接进 `CADDedupPlugin.Client.Tests`（`net6.0-windows`）。
  - Helper 两条路由 `/material/assistant/resolve` + `/material/assistant/create` 及 `ForwardBusinessAsync` 接线（`Helper`，net6.0），`Helper.Tests` 覆盖。
  - 契约测试：client 走 helper（非直连 PLM）、helper→PLM 路径正确、Bridge 严格 call shape。
- **延后 manual（AutoCAD SDK，Windows 操作签核，不进 CI）**：
  - `PLMMATASSIST` 命令本体（`DedupPlugin.cs`）+ 结果展示窗口。仿 S8 做法用静态校验脚本 + Windows 验收模板，不在 CI 构建。

> 这条决定了"实现完成"的定义：**SDK-free 部分 CI 全绿**即为 Phase 3 主体达成；AutoCAD 命令随 Windows 签核单独交付。

## 3. 现状与复用基线（grounding，三层转发）

- **AutoCAD client** `clients/autocad-material-sync/CADDedupPlugin/MaterialSyncApiClient.cs`
  - helper-forwarded 范式：`_helperTransport.PostJsonAsync<MaterialSyncResponse>("/sync/inbound", payload)`（:117）；`/sync/outbound`（:135）；`/diff/preview`→`HelperDiffPreviewResponse` 包 `pull_id`+`server_response`（:155-166）。
  - direct 范式（**未迁移**）：`ComposeAsync`/`ValidateAsync`/`GetProfilesAsync` 用 `_httpClient.SendAsync($"{BasePath}/compose")`（:79-81/:96-98/:55）。
  - 注入点：`IMaterialSyncHelperTransport _helperTransport`（:25），构造可注入 fake（:33）。
- **Bridge**（LISP facade，不定义 HTTP 路由）`clients/cad-desktop-helper/Bridge/`：`BridgeCallService`（`yuantus-helper-call`/`upload`）→ 定位 helper → `SharedBridgeTransport.PostJsonAsync(endpoint, payload)` 转发；严格 call shape 由 `Bridge.Tests/BridgeContractTests.cs`（:311-319）锁定。
- **Helper server** `clients/cad-desktop-helper/Helper/HelperRuntime.cs`
  - 路由注册：`app.MapPost("/sync/inbound", …)`（~:3372），同区有 `/diff/preview`/`/sync/outbound`/`/audit/apply-result`。
  - 转发逻辑：`HelperBusinessAuditService.ForwardBusinessAsync(helperEndpoint, plmEndpoint, request, ct)`（~:3037）读 session（serverUri+bearer）→ `_plm.PostAsync(serverUri, plmEndpoint, …)` → 写审计。
- **迁移态**（`docs/DEV_AND_VERIFICATION_CAD_HELPER_BRIDGE_S8_MATERIAL_SYNC_MIGRATION_R1_20260523.md`）：`/diff/preview`、`/sync/inbound`、`/sync/outbound`、`/audit/apply-result` 已走 helper；`/compose`、`/validate`、`/profiles` 仍直连。
- **命令范本** `clients/autocad-material-sync/CADDedupPlugin/DedupPlugin.cs`：`PLMMATPULL`（:585-668）= 读字段→`DiffPreviewAsync`→展示窗口→确认→`ApplyFields`→`/audit/apply-result` 上报。`PLMMATASSIST` 仿此。

## 4. 七条约束 / 设计要点

1. **assistant 两路由都走 helper-forwarded**（不直连 PLM）——与 S8 的 sync/diff 一致，client 不出现 `{BasePath}/assistant`。
2. **不迁移 `/compose`/`/validate`**：assistant `resolve` 在 PLM 服务端已内联 compose/validate/match/similar（Phase 2），client 只需发 `cad_fields`/`values`，**无需** client 端 compose/validate，故本阶段不动那两条直连路由。
3. **resolve 只读语义沿用 diff/preview 范式**：`/diff/preview` 也是只读且走 helper 审计；`/material/assistant/resolve` 同样经 `ForwardBusinessAsync`（审计记录调用，PLM 侧零写入由 Phase 2 保证）。
4. **create 经 helper-forwarded**，确认后才调；helper 审计记录写操作（与 sync/inbound 一致）。
5. **PLM 路径常量**：helper 侧映射 `/material/assistant/resolve`→`/plugins/cad-material-sync/assistant/resolve`，`/material/assistant/create`→`/plugins/cad-material-sync/assistant/create`。
6. **DTO**：新增 `MaterialAssistantResolveResponse`（composed_properties / exact_matches / similar_candidates[含 score, high_similar, field_contributions] / draft_suggested / warnings）与 `MaterialAssistantCreateResponse`（item_id / item_number / state / current_state / draft_check / errors / warnings），与 Phase 2 服务端响应字段对齐。
7. **dedup_vision 不进本阶段**（与 Phase 2 一致，flag 后置）。

## 5. 落点清单

CI-可测（SDK-free）：
- `clients/.../CADDedupPlugin/MaterialSyncApiClient.cs`：`ResolveAsync(...)`、`CreateAsync(...)`（helper-forwarded）+ 两个 DTO。
- `clients/cad-desktop-helper/Helper/HelperRuntime.cs`：两条 `app.MapPost("/material/assistant/*", …)` + `HelperBusinessAuditService` 两个 `ForwardBusinessAsync(...)` 方法 + 两个 PLM 路径常量。
- `clients/.../CADDedupPlugin.Client.Tests/MaterialSyncClientS8ContractTests.cs`（或新 `…AssistantContractTests.cs`）：断言 Resolve/Create 走 `/material/assistant/*` 且不含 `{BasePath}`；并把路由计数 pin 改 17（:22）。
- `clients/cad-desktop-helper/Helper.Tests/…`：helper 路由→正确 PLM 路径、**local-token gate（无 token→`AuthLocalTokenMissing`）**、session 缺失处理；三个计数 pin 改 17（HelperBusinessAudit/HelperSessionRoutes/HelperResetLocalToken）。
- `clients/cad-desktop-helper/Bridge.Tests/BridgeContractTests.cs`：**无需改 Bridge 代码**，新增一条 `/material/assistant/resolve` 的 call-shape contract；路由计数 pin 改 17（:484）。

延后 manual（AutoCAD SDK）：
- `clients/.../CADDedupPlugin/DedupPlugin.cs`：`[CommandMethod("PLMMATASSIST")]` + 结果窗口（仿 `MaterialSyncDiffPreviewWindow`）。
- 静态校验脚本（仿 `verify_material_sync_static.py`）+ Windows 验收模板。

## 6. 测试与 CI

- SDK-free 契约/单测全部纳入 `cad-helper-shared-dotnet.yml` 现有步骤（Client.Tests / Helper.Tests / Bridge.Tests 已在跑；新测试加入相应工程即随之执行 —— 确认新测试文件被 csproj glob 收录，避免静默不跑）。
- 契约要点（仿 S8 `MaterialSyncClientS8ContractTests.cs:32-42`）：
  - `ResolveAsync`/`CreateAsync` 经 `IMaterialSyncHelperTransport`，记录 endpoint == `/material/assistant/resolve`|`/create`，payload 形状正确；断言 client **不含** `{BasePath}/assistant`（非直连）。
  - **Helper local-token/security gate（必须，不止 session 缺失）**：`/material/assistant/*` 纳入本地令牌门 —— 无 `X-Yuantus-Local-Token`（`HelperRuntime.cs:3290`）返回 `ErrorCodes.AuthLocalTokenMissing`（`:1010`）；带 token 才 allowed。
  - helper 侧路径映射：`/material/assistant/resolve`→`/plugins/cad-material-sync/assistant/resolve`、`/create` 同理；session 缺失返回既有错误形态。
  - **Bridge（大概率零代码改）**：Bridge 是 generic `CallAsync(endpoint, payload)`，无需为新路由改代码；但**必须新增一条 contract**用 `/material/assistant/resolve` 验证 locator 先于 transport、endpoint 原样、payload byte-for-byte（证明新路由能经 LISP facade 走通，而非只复用旧 `/diff/preview` 的断言）。
- **路由总数 pin 由 15 → 17（确定，必改）**：以下断言 `MapGet+MapPost` 计数 == 15 的测试全部更新到 17 ——
  - `Helper.Tests/HelperBusinessAuditContractTests.cs:40`
  - `Helper.Tests/HelperSessionRoutesContractTests.cs:306`
  - `Helper.Tests/HelperResetLocalTokenContractTests.cs:311`
  - `Bridge.Tests/BridgeContractTests.cs:484`
  - `CADDedupPlugin.Client.Tests/MaterialSyncClientS8ContractTests.cs:22`
- **不**在 CI 构建 `CADDedupPlugin.csproj`（无 AutoCAD SDK）——`PLMMATASSIST` 由静态校验 + Windows 签核覆盖。

## 7. 排期（每步以绿测试为门）

1. Helper 两路由 + `ForwardBusinessAsync` 接线 + `Helper.Tests`。
2. `MaterialSyncApiClient.ResolveAsync/CreateAsync` + DTO + `Client.Tests` 契约。
3. Bridge 端点校验（若需）+ helper 路由计数类合同测试更新。
4. `PLMMATASSIST` 命令 + 静态校验脚本（**延后 manual**，不阻塞 CI 主体）。
5. CI 全绿（SDK-free）+ Windows 验收模板交接。

## 8. 边界 / 非目标

- 不迁移 `/compose`/`/validate`/`/profiles`（assistant 不依赖）。
- 不做 dedup_vision 图纸相似（flag 后置）。
- 不改 Phase 2 的 PLM 服务端行为（仅转发）。
- SolidWorks/ZWCAD/GstarCAD 命令入口不在本阶段（同一 helper 路由后续复用）。

## 9. 出口

Phase 3 SDK-free 部分合并后，物料助手在 **SDK-free client API ↔ Helper ↔ PLM 三层贯通**；**AutoCAD 命令入口（`PLMMATASSIST`）随 Windows 签核交付**（此前用户在图纸里的端到端体验尚未达成，须待该签核）。后续可选：dedup_vision 图纸相似（需上传链路）、其它 CAD 客户端入口复用同一 helper 路由。
