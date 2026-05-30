# OdooPLM (OmniaGit/odooplm) 落地对比与差距分析（实证版）

Date: 2026-05-25
Scope: YuantusPLM（元图PLM）核心服务 vs OmniaSolutions `OmniaGit/odooplm`
Method:
- **Yuantus 侧**：逐模块**源码深读**取证（`file:line`）；区分"读函数体 / 读签名模型路由 / 仅体量或命名"三档置信度。
- **odooplm 侧**：基于**公开仓库模块清单 + README/官网 + 少量关键文件抽样**，**未克隆其代码逐行核对**；故本文对 odooplm 的描述限于"模块作用 / 功能声明"，不对其实现深度背书（表格 odooplm 列勿当作高置信源码结论）。

License note（**已逐源抽样核验**）: odooplm 许可证**非单一 LGPL-3**——`plm/__manifest__.py` 声明 LGPL-3，但部分源文件文件头为 **AGPL-3 or later**（已核验 `plm/models/plm_mixin.py`，见 §10 外部源）。**本文未做全量逐文件许可证审计**，故按"可能含 AGPL"从严：本文所有对照均为 **流程 / 契约 / 命令集 / 数据模型语义** 层面的"语义覆盖 / 功能对齐"，**严禁代码搬运或派生实现**（与 `docs/REUSE.md` 一致）。

置信度图例：〔高〕读了函数体 ・〔中〕读了签名/模型/路由/测试规模 ・〔低/❓〕仅命名或未打开。
状态图例（仅指 **API/模型/路由层是否落地**，**不代表**"已对齐 odooplm 生产级行为闭环"——后者见 §4 置信度与 §8）：✅已落地 ・🟡部分 ・❌缺失 ・❓待证实。

---

## 0. 头条洞察（最该投资的地方）

> **服务端 PLM 原语已经齐全**：`AMLEngine` 提供完整 Aras 式 RPC——`rpc_check_out / rpc_check_in / rpc_undo_check_out / rpc_eco_apply / rpc_compare_bom / rpc_get_bom_structure / rpc_run_method`（`src/yuantus/meta_engine/services/engine.py:499-570`），`CadBomImportService.import_bom` 能接树形 BOM payload（`services/cad_bom_import_service.py:235-292`），后端 `cad_checkin_router`(prefix `/cad`) 已提供 **item-scoped** checkout / undo-checkout / checkin / checkin-status 路由（`web/cad_checkin_router.py:129/157/169/233`）。
>
> **真正缺的是"最后一英里"**：跨 CAD 系统、产品化、能从 CAD 内一键驱动整装配检入/检出/产品搜索/BOM 上传的**客户端命令层**。投资方向 = 把 CAD 侧命令（LISP / COM / .NET addin）接到这些已存在的后端原语上，并在 SolidWorks / SolidEdge / Inventor / AutoCAD 间统一，**不是再建后端**。

⚠️ 措辞纪律：不要因为 `AMLEngine` 有 `rpc_check_out` 就宣称"检入检出与 odooplm 持平"。odooplm 的优势不是"后端能否记录检入"，而是**坐在 CAD 里一键检入整个装配**的工程闭环——而 Yuantus 的 `cad-desktop-helper` 当前**没有 checkout/checkin 路由**，LISP 命令为 display-only（见 `docs/DEV_AND_VERIFICATION_CAD_HELPER_BRIDGE_S10_LISP_SHELL_R1_20260524.md`，明确 "no DWG mutation"）。

---

## 1. 关系定位

- **范式不同**：odooplm 是寄生于 Odoo 的 ERP 插件（多年生产装机、Odoo App Store 上架）；Yuantus 是独立的、元数据驱动（meta_engine / Aras 式 item type）的现代异步服务（FastAPI + PostgreSQL + worker）。
- **谱系明确**：`docs/REFERENCE_NOTES.md` / `docs/REUSE.md` 记载 Yuantus 参考 odoo18-enterprise plm、erpnext、docdoku、旧 PLM/src 重写而来。odooplm 的模块名（breakages / cutted_parts / box / compare_bom / pack_and_go / document_multi_site / workflow_custom_action / consumption_plans …）与 Yuantus 子系统**高度一一对应**。
- **结论先行**：实测表明 Yuantus 已在**功能/语义层覆盖**（独立实现，非代码移植）了 odooplm 的绝大多数业务模块，且在 **生效性、多租户、元引擎** 上反而更强。差距收敛为两条半（见 §5）。

---

## 2. odooplm 全模块 → Yuantus 落地映射（核心表）

> odooplm 模块清单取自仓库根目录（40+ Odoo addon）。Yuantus 侧逐条给 `file:line` 证据与状态。

| odooplm 模块 | 作用 | Yuantus 对应（证据） | 状态 | 置信 |
|---|---|---|---|---|
| `plm`（核心） | PLM 基础对象/文档/检入 | `AMLEngine` 元引擎 `services/engine.py:31`；Item/ItemType/Property `meta_engine/models/` | ✅ | 高 |
| `plm_engineering` | ECO/工程变更 | `services/eco_service.py`(3214行) + `models/eco.py` | ✅ | 高 |
| `plm_compare_bom` | BOM 对比（3 模式） | `services/bom_service.py` compare + `plugins/yuantus-bom-compare` | ✅ | 高 |
| `plm_bom_summarize` | BOM 汇总 | `services/bom_rollup_service.py` + 座舱 `product_service.py:524` | ✅ | 中 |
| `plm_automate_normal_bom` | EBOM→普通 BOM | `services/bom_conversion_service.py` | ✅ | 中 |
| `plm_date_bom` | 按日期生效 BOM | `services/effectivity_service.py:170-184`（Date 类型）**更强** | ✅ | 高 |
| `plm_spare` | 备件 | （`spare`→0 文件） | ❌ | 高 |
| `plm_cutted_parts` | 下料件 | `meta_engine/cutted_parts/`(1939行, `RawMaterial`/`MaterialType`) | ✅ | 中 |
| `plm_breakages` + `plm_ent_breakages_helpdesk` | 破损/工单台 | `meta_engine/maintenance/` + `parallel_tasks_service.py` 破损 helpdesk | ✅ | 中 |
| `plm_box` | 装箱 | `meta_engine/box/`(1513行) | ✅ | 中 |
| `plm_pack_and_go` | 打包导出 | `plugins/yuantus-pack-and-go` | ✅ | 中 |
| `plm_web_3d` / `plm_web_3d_sale` | 3D Web 查看 | overlay/markup `web/parallel_tasks_cad_3d_router.py:61`(`/cad-3d/overlays`)；视图态 `cad_view_state_router.py`；几何 OBJ/glTF。**视觉爆炸图(explode) 未见** | 🟡 | 高 |
| `plm_web_revision` | web 端改版 | 版本服务 + `engine.py:277` `rpc_create_branch` | ✅ | 中 |
| `plm_automated_convertion` | 自动转换 | `services/cad_converter_service.py`(885) / `cadgf_converter_service.py` / `services/cad-extractor` | ✅ | 高 |
| `plm_automatic_weight` | 重量自动汇总 | `services/bom_rollup_service.py:22-53` `compute_weight_rollup`（含环检测+回写） | ✅ | 高 |
| `plm_auto_engcode` | 工程码自动生成 | `services/numbering_service.py`（仅前缀+补零，无工程码语义） | 🟡 | 高 |
| `plm_auto_internalref` | 内部参考号 | `services/numbering_service.py`（同上） | 🟡 | 高 |
| `plm_auto_translator` / `plm_report_language_helper` / `plm_product_description_language_helper` | 多语言 | `meta_engine/locale/` + `meta_engine/report_locale/` | ✅ | 中 |
| `plm_workflow_custom_action` | 工作流自定义动作 | 规则匹配在 `parallel_tasks_service.py:2184` `_normalize_match_predicates` / `:2259` `_rule_matches_runtime_scope`，算术委托纯契约 `automation_rule_predicate_contract.evaluate_rule_predicate` | ✅ | 中 |
| `activity_validation` | 活动校验门 | ECO 活动门 / `parallel_tasks` 活动门 | ✅ | 中 |
| `plm_suspended` | 挂起态 | `services/suspended_guard.py`(174行)+测试 258行 | ✅ | 中 |
| `plm_product_only_latest` | 仅引用最新已发布 | `services/latest_released_guard.py:1-60`+测试 374行（**PLM 级**） | ✅ | 高 |
| `plm_consumption_plans` | 消耗计划 | `ConsumptionPlanService`(`parallel_tasks_service.py:2634`)+`models/parallel_tasks.py:134` `ConsumptionPlan`+`web/parallel_tasks_consumption_router.py`（含模板版本/影响预览） | ✅ | 高 |
| `plm_pdf_workorder` / `plm_pdf_workorder_enterprise` | PDF 工单 | `web/parallel_tasks_workorder_docs_router.py:48` `_manifest_to_pdf_bytes`（真生成 `%PDF-1.4`+xref） | ✅ | 高 |
| `plm_document_multi_site` + `mirror_document_server` | 多站点文档镜像 | `meta_engine/document_sync/`（`SyncSite`/`SyncJob`/`SyncRecord` + PUSH/PULL + 站点鉴权 `models.py:70/108/161`） | ✅ | 中 |
| `plm_client_customprocedure` | CAD 客户端自定义过程 | `clients/cad-desktop-helper`（会话/当前图纸/diff/同步/audit 路由，**无 checkout**；dedup 走 legacy direct 非 helper；LISP display-only） | 🟡 | 高 |
| `plm` 之 `plm_material`/`plm_finishing`/`plm_treatment`/`plm_descriptions` | 材料/表面处理/热处理属性 | `cutted_parts` `RawMaterial`/`MaterialType` + CAD 材料同步（`finishing`→0、`treatment`→1 文件） | 🟡 | 中 |
| `plm_project` | 与 Odoo 项目集成 | （`project`→3 文件，非完整集成） | 🟡 | 中 |
| `plm_purchase_only_latest` | 采购仅最新版本 | ❌ 无 ERP 采购交易面 | ❌ | 高 |
| `plm_sale_only_latest` | 销售仅最新版本 | ❌（`sale`→0 文件） | ❌ | 高 |
| `plm_purchase_share` | 采购共享 | ❌ 无 ERP | ❌ | 中 |
| `step_tree_viewer.py` | STEP 结构树查看 | `services/cad-extractor` / `shape_service.py` | 🟡 | 中 |

**统计**：约 33 个对标项中，✅ **API/模型层已落地** ≈ 20，🟡 部分 ≈ 7，❌ 缺失 ≈ 4（均与 ERP/备件相关），❓ 0。**注意**：✅ 仅表示接口/模型/路由已落地，**不等于**已对齐 odooplm 的生产级行为闭环（后者见 §4 置信度 / §8）。

---

## 3. 撤回：第一轮判错的地方（实证推翻）

| 第一轮结论 | 实测 | 证据 |
|---|---|---|
| 自动编号"基础（A-Z/1.2.3）" | ❌ 错。租户/组织级、**并发安全**序列分配器（PG `on_conflict_do_update`+`greatest`、SQLite upsert、通用乐观重试、floor 兼容历史数据）。仅 pattern 词汇窄 | `services/numbering_service.py:58-178`〔高〕|
| effectivity"弱，仅起止日期" | ❌ 错。**Date/Lot/Serial/Unit 四类 + BOM 按生效过滤**，强于 odooplm `plm_date_bom` | `services/effectivity_service.py:158-249`〔高〕|
| "无 suspended 态" | ❌ 错。`suspended_guard` 已实现 | `services/suspended_guard.py`〔中〕|
| "`*_only_latest` 语义缺失" | ⚠️ 部分错。**PLM 级**已实现；缺的只是下游 ERP 交易面 | `services/latest_released_guard.py`〔高〕|
| "借鉴 automatic weight" | ❌ 已做 | `services/bom_rollup_service.py`〔高〕|
| "borrow consumption_plans" | ❌ 已做 | `parallel_tasks_service.py:2634`〔高〕|
| "borrow pdf_workorder" | ❌ 已做 | `parallel_tasks_workorder_docs_router.py:48`〔高〕|
| "3D 无 markup" | ⚠️ 偏。overlay/markup + 视图态 + 评审已有；仅视觉**爆炸图**未见 | `parallel_tasks_cad_3d_router.py:61`〔高〕|
| "work_order 是 stub" | ⚠️ 措辞错。不是 stub，是**无原生 WO 引擎**（设计交给外部），仅 gate/bridge/PDF 契约 | 无 `work_order_service.py`；`quality_workorder_gate_contract.py` 等〔高〕|
| "ES 仅设计未实现" | ⚠️ 偏。ES 客户端+DB fallback 是真代码；准确说法="已实现但未经规模验证" | `services/search_service.py:17-48,430-467`〔高〕|

> 佐证整体成熟度：全仓非测试代码仅 **4 处 `NotImplementedError`、0 处 `TODO/FIXME`**；测试极厚（eco 194 / workflow 141 / cad 112 / routing 111 / tenant 108 个测试文件）。**它不是脚手架。**

---

## 4. 能力域"声称 vs 实测"深度（重点域）

| 域 | 实测深度 | 证据 | 置信 |
|---|---|---|---|
| meta_engine 元引擎 | **真 Aras 式 RPC 引擎**：create/write/search/get_bom_structure/run_method/create_branch/version_tree/eco_apply/check_out/in/compare_bom/flatten_bom/schema_get_definition/app_store_install | `services/engine.py:31,499-570` | 高 |
| 表达式/规则 DSL | **薄**：`parsers/` 仅 Odoo ir.rule 域过滤适配器(162 行)；规则匹配算术已抽取为纯契约 `evaluate_rule_predicate`，由 `parallel_tasks_service.py:2259` `_rule_matches_runtime_scope` 委托（旧 `_rule_match_predicates` 已删、有测试守护），但仍**无面向用户的 DSL** | `parsers/ir_rule_adapter.py`；`test_automation_rule_predicate_contract.py:428` | 高 |
| BOM | EBOM(关系即 Item) + MBOM 转换 + 对比 + where-used + obsolete rollup + 重量 rollup + 数据级 explode | `bom_service.py`(2104)、`bom_rollup_service.py`、`web/query_router.py:88` | 高 |
| 生效性 | Date/Lot/Serial/Unit + BOM 过滤 + latest/suspended guard | `effectivity_service.py` | 高 |
| 检入检出 | 服务端原语齐全（AMLEngine + 版本服务 + cad_checkin_router 的 item-scoped checkout/undo/checkin/status）；**CAD 内一键闭环缺（helper 客户端侧无 checkout）** | `engine.py:499-555`；`web/cad_checkin_router.py:129/157/169/233` | 高 |
| 多站点文档同步 | SyncSite/Job/Record + PUSH/PULL + 站点鉴权 | `document_sync/models.py`、`service.py:60` | 中 |
| 搜索 | ES 客户端 + DB fallback + 增量索引器（未经规模验证） | `search_service.py`、`search_indexer.py` | 高/中 |
| 电签 / SLA-升级 | 仅按体量判断：`esign/`(900) HMAC（声称）、`parallel_tasks_service.py`(**12,625 行**)承载 SLA/升级/活动门/破损台/消耗计划/CAD-3D/工单 PDF——**未深读，不据此记功能分** | — | 低 |
| 对标业务模块 | box/cutted_parts/quality/subcontracting/maintenance/dedup：**API/模型层已落地**、0 NotImplemented；**行为闭环未深度审计** | def 计数 + 测试存在 | 中 |

---

## 5. 真实差距（细化 + 影响×可修复性 排序）

| # | Gap | 影响 | 可修复 | 依据 |
|---|---|---|---|---|
| **G1** | **CAD 客户端"最后一英里"**：产品化、跨 CAD、从 CAD 内驱动整装配检入/检出/产品搜索/BOM 上传 | 高 | 中（后端已就位） | helper 仅 10 条路由（`/healthz /version /session/* /cad/current-drawing /diff/preview /sync/inbound|outbound /audit/apply-result`），**无 checkout/checkin**；dedup 仍走 legacy direct（`DedupApiClient`→`/api/dedup/check`，测试守护"不得进 helper、route count==10"）；LISP display-only〔高〕|
| **G2** | **PLM→ERP 下游交易面**：已发布件自动流向采购/销售/库存/生产；`purchase/sale_only_latest`、`purchase_share` 无对应 | 高 | 中（需定对接策略，**非自建 ERP**） | guard 原语已有但无 ERP 交易面；`sale`→0；`purchase`→4 处均为**非 ERP 偶发命中**（app-store 许可购买 `store_router.py:34`、维保资产 `purchase_date` `maintenance/models.py:124`、docstring 举例），**未见采购订单/采购共享 surface**〔高〕|
| **G3** | **3D 视觉爆炸图(explode)**（markup/overlay 已有） | 中 | 中 | 仅数据级 `/bom/explode`；3D overlay 有，spatial explode 未见〔高〕|
| **G4** | **编号 pattern 词汇**：日期/分类/多段 token | 低 | 高 | 现仅"前缀+补零"`numbering_service.py:63`〔高〕|
| **G5** | **plm_spare 备件** | 中 | 高 | `spare`→0 文件，确为真缺失〔高〕|
| **G6** | **生产装机量 / 规模化验证** | 高 | 低（无快速修复） | odooplm 多年生产+App Store；Yuantus v0.1.x。**与代码完整度正交**〔高〕|
| minor | finishing/treatment 工艺属性、`plm_project` 完整集成 | 低 | 高/中 | `finishing`→0、`treatment`→1、`project`→3 文件〔中〕|

> **状态更新（2026-05-27，仅标注，不改原始分析）**
> - **G1**：CAD helper "最后一英里" **软件侧已闭环**（helper checkout/undo/status/checkin/bom-import 路由 + 六条 LISP 命令 + `yuantus-helper-upload` 多部传输缝；路由计数 15）。详见 `DEV_AND_VERIFICATION_CAD_HELPER_BRIDGE_LAST_MILE_CLOSEOUT_20260527.md`(#662)。残留仅 native-CAD 真机 operational signoff（硬件/操作员）与产品化/跨-CAD 覆盖。
> - **G2**：**PLM→ERP publication contract 为下一主线**，程序计划见 `DEVELOPMENT_PLM_TO_ERP_PUBLICATION_CONTRACT_PLAN_20260527.md`（outbound 投放契约，不自建 ERP、不绑 Odoo）。

> **状态更新（2026-05-30，仅标注，不改原始分析）**
> - **G2**：PLM→ERP publication spine **已功能闭环**：readiness API、outbox/routes/worker、generic HTTP connector、read-only export 已落 `main`（#663-#676）。vendor-specific adapter 仅在有明确目标 ERP 时另起 taskbook。
> - **G3**：3D visual explode **thin server surface 已落地**：validated explode config 存于 `meta_3d_overlays.properties["explode"]`，无 migration/table/model；BOM-derived auto-layout 与 multi-preset table 仍 deferred。详见 `DEV_AND_VERIFICATION_ODOOPLM_G3_3D_EXPLODE_IMPL_20260530.md`(#682)。
> - **G4**：numbering pattern v1 **已落地**：literal + UTC date + trailing `{seq}` token 复用既有 prefix allocator，无 migration/route/schema change；category/property token 仍 deferred。详见 `DEV_AND_VERIFICATION_ODOOPLM_G4_NUMBERING_PATTERN_IMPL_20260529.md`(#680)。
> - **G5**：spare parts **已落地**：基于 `ItemType(is_relationship=True)` 关系模型实现 `Part Spare`，无 bespoke table/migration。详见 `DEV_AND_VERIFICATION_ODOOPLM_G5_SPARE_PARTS_IMPL_20260529.md`(#678)。
> - 当前 ledger 刷新见 `DEVELOPMENT_ODOOPLM_GAP_LEDGER_REFRESH_20260530.md`；§2 原模块表保留为 2026-05-25 历史快照，不追溯改写。

---

## 6. 借鉴与建议（可落地，License 边界内）

> 全部为语义/契约/命令集对齐，非代码搬运。

1. **【G1·最高优先】CAD 客户端"最后一英里"**
   - 把 `clients/autocad-material-sync` + `clients/solidworks-material-sync` + `clients/cad-desktop-helper` **收敛为一个产品化客户端**，并提供一张**公开命令清单页**（对齐 odooplm `/download-clients` + `/client` 的产品工程模式）。
   - roadmap 先做最高价值闭环：**从装配树读 BOM → 上传全部关联文件 → 整树检入**；CAD 侧命令直接调用已存在的 `rpc_check_out/in`、`CadBomImportService.import_bom`、`cad_checkin_router` 的 item-scoped checkout/checkin 路由。
   - 给 helper 增补 `/checkout`、`/checkin` 路由 + 对应 LISP/COM 命令（当前为 display-only）。
2. **【G2·高】PLM→ERP 投放契约**
   - 定义 released item → product/BOM 投放契约；把已有的 `latest_released_guard` / `suspended_guard` 语义延伸到 ERP 交易面（采购/销售/库存行始终解析最新已发布版本）。对接外部 ERP（或 Odoo），**不自建 ERP**。
3. **【G3·中】3D 视觉爆炸图**：在已有 overlay/view-state 基础上补 spatial explode（markup 已具备）。
4. **【G4·低】编号 pattern**：把 `numbering_service` 升级为可配置 token 规则（日期/分类/多段），对齐 `plm_auto_engcode`/`internalref`。
5. **【G5·中】备件模块**：新增 spare（爆炸备件视图/备件目录），可作为 meta_engine ItemType + 关系实现。
6. **【G6·高，无代码捷径】**：建立"参考客户/试点部署"积累真实装机验证——这条只能靠部署，不能靠写代码补齐，需如实对外标注。

---

## 7. 我们已经领先 / 不必照搬

- **真领先**：meta_engine 元数据驱动（`AMLEngine` Aras 式 RPC，租户可自定义 ItemType/关系）——odooplm 受 Odoo ORM 约束，结构性弱于此；**多租户**（`TenantOrgContextMiddleware` + 租户级 schema 隔离）；**生效性**（四类 effectivity）强于 odooplm。
- **方向更现代但需标"未经规模验证"**：异步 FastAPI + worker + circuit breaker、契约化微服务（cad-extractor/cad-connector/dedupcad-vision ML）、e-sign、ES 搜索、CI strict gate。
- **结论**：借鉴 odooplm 的功能点与契约语义，但**不要为了"像它"而把自己改成 Odoo 插件**——独立 + 元引擎 + 多租户是差异化优势，应保留。

---

## 8. 置信度与未核实声明（诚实边界）

- 〔低〕项（`esign` HMAC 行为、`parallel_tasks_service` 12,625 行内部、`baseline_service` 1017 行）**仅凭体量**，未深读，不据此记功能分。
- 对标业务模块（box/cutted_parts/quality/subcontracting/maintenance/dedup）仅凭 def 计数 + 0 NotImplemented + 测试存在判断"API/模型层已落地"，**未深度审计行为闭环正确性**（不等于已对齐 odooplm 生产水平）。
- 第一轮子代理 12 域总表中的"无 SAML/OAuth2、无字段级权限、无插件沙箱"等论断，**本轮未重新核实**，据此决策前需另起验证。
- odooplm 侧依据其公开仓库模块清单 + 官网功能描述 + README，未克隆其代码逐行核对客户端实现。

---

## 9. 附录：取证方法（可复现）

```
# 体量
find src/yuantus/meta_engine/<sub>/ -name '*.py' | xargs wc -l
# stub 标记（全仓非测试）
grep -rIn --include='*.py' -E "NotImplementedError|# *TODO|# *FIXME" src/yuantus | grep -iv test
#   → 4 处 NotImplementedError，0 处 TODO/FIXME
# 测试规模
grep -rIl "def test_" src/yuantus | xargs grep -lIi "<keyword>" | wc -l
# 模块存在性
grep -rIl --include='*.py' -iE "spare|consumption|sale|purchase" src/yuantus/meta_engine
```

关键证据文件：
- `src/yuantus/meta_engine/services/engine.py`（AMLEngine）
- `services/numbering_service.py` / `effectivity_service.py` / `bom_rollup_service.py`
- `services/latest_released_guard.py` / `suspended_guard.py`
- `document_sync/models.py` / `service.py`
- `web/parallel_tasks_cad_3d_router.py` / `parallel_tasks_workorder_docs_router.py` / `parallel_tasks_consumption_router.py`
- `clients/cad-desktop-helper/`（路由面 + S10 LISP 任务书）

## 10. Sources（odooplm 侧）

- OmniaGit/odooplm: https://github.com/OmniaGit/odooplm
- OdooPLM 官网 / 客户端下载 / 命令索引: https://odooplm.omniasolutions.website/ ・ /download-clients ・ /client
- odooplm | OmniaSolutions: https://omniasolutions.website/page/odooplm
- **许可证证据**（§0 License note 依据）:
  - 模块清单（LGPL-3）: https://github.com/OmniaGit/odooplm/blob/19.0/plm/__manifest__.py
  - 文件头（AGPL-3 or later）: https://github.com/OmniaGit/odooplm/blob/19.0/plm/models/plm_mixin.py
