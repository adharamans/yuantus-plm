# YuantusPLM × OdooPLM(odooplm-19.0)逻辑差距与借鉴分析

> 生成日期:2026-06-04 · 作者:工程分析(深读两套代码后产出)
> 对比对象:
> - **本系统**:`YuantusPLM`(`/Users/chouhua/Downloads/Github/Yuantus`,模块化单体,FastAPI + SQLAlchemy,`src/yuantus/meta_engine/`)
> - **参考系统**:`odooplm-19.0`(`/Users/chouhua/Downloads/odooplm-19.0`,**OmniaSolutions** 出品的 Odoo 19 PLM-PDM 插件集,核心模块 `plm/`)

---

## 0. 一句话结论

**两者不是同一物种**:Yuantus 是「**Aras 风格的企业级元模型 PLM 引擎**」——通用 Item 模型、关系即对象、可插拔版本方案、可配置生命周期/工作流引擎、ECO/审批/电子签名/MBOM/基线/有效性/ERP 发布,**面广**;odooplm-19.0 是「**以桌面 CAD 为中心的务实 PDM+PLM 插件集**」——和 SolidWorks/Inventor/SolidEdge 等深度集成,**很多 CAD-PDM 领域逻辑是被产线打磨过的、具体而成熟的**。

因此本报告的价值不在于"谁覆盖的功能多"(Yuantus 在治理/制造/合规维度的**子系统数量**明显更多),而在于:**OdooPLM 这套 CAD-PDM 领域逻辑,正好暴露了 Yuantus 抽象引擎尚未长出的几处具体能力**。这才是真正值得借鉴的净增量。

> ⚠️ **诚实警示(广 ≠ 深)**:Yuantus 拥有大量"专有子系统",但本次探查的 digest 同时标注了 workflow voting "partial"、permission enforcement "stubbed"、ECO conflict resolution "stub"、dynamic assignment "incomplete",以及大量 `Cxx bootstrap verified`(CRUD+analytics 脚手架)。本报告对"Yuantus 已领先"的判断,凡未逐行核验的,一律降级为"**存在专有子系统,深度未审计**"。请勿把本报告读成"我们大体领先"——那不是这份差距分析的目的。

---

## 1. 前置澄清:odooplm-19.0 ≠ 仓库文档里的"Odoo18 PLM"

仓库 `docs/` 里已有一批对标"**Odoo18 PLM**"的 parity 文档(如 `DESIGN_PARALLEL_ODOO18_REFERENCE_PARITY_FINAL_SUMMARY_20260407.md`、`CAPABILITY_BENCHMARK_CHECKLISTS_20260321.md`)。那是一个**概念基准**,对应的是 Box / Document-Sync / Cutted-Parts 等"Odoo18 风格"能力线的有界增量。

本次实际深读的 `odooplm-19.0` 是 **OmniaSolutions 的真实 CAD-PDM 插件集**,与上面那条概念基准**不是同一来源**。它给我们带来的"净新增参考料",几乎全部集中在 **CAD-PDM 工程域**:

- 文档↔文档关系图(装配 / 图纸 / 引用 / 打包)
- 工作站级 check-in/check-out
- 2D↔3D 时效一致性(图纸相对模型"过期"检测)
- pack-and-go 交付包导出

**所以本报告定位为对现有 Odoo18 parity 文档的"补充",而非重复**:它聚焦那条概念基准里没覆盖、而 OmniaSolutions 插件里有的 CAD-PDM 真实逻辑。

---

## 2. 两系统定位 / 架构总览

| 维度 | YuantusPLM | odooplm-19.0 (OmniaSolutions) |
|---|---|---|
| 形态 | 模块化单体服务 (FastAPI/SQLAlchemy) | Odoo 19 插件集(核心 `plm/` + 35 个 `plm_*`) |
| 核心数据模型 | **单表继承**:`meta_items`(一切皆 Item,`item_type_id` 判别) | 三张一类对象:`product.product`(零件)、`ir.attachment`(文档/CAD)、`mrp.bom`(BOM) |
| BOM 建模 | **关系即 Item**(`is_relationship` 类型,`source_id`/`related_id`,属性进 JSONB) | 专用 `mrp.bom` + `mrp.bom.line` 表 |
| 版本模型 | **双轨 Generation.Revision**,可插拔方案(letter/number/hybrid/semantic) | 整型 `engineering_revision` + 字母换算 + 分支 `engineering_branch_revision` |
| 生命周期 | 可配置状态机 + hooks + 条件 DSL + 身份权限(`LifecycleService`) | 固定 5 态(draft/confirmed/released/undermodify/obsoleted),命名方法转移(`plm_mixin.py`) |
| 工作流/变更 | 通用工作流引擎 + 通用审批框架 + ECO + 电子签名子系统 | `activity_validation`(ECR/ECO 活动)+ `plm_workflow_custom_action`(状态钩子) |
| CAD 集成 | Web 优先:CAD 导入/预览/属性/网格/转换 routers(`web/cad_*`) | **桌面优先**:与 CAD 编辑器双向插件、PWS 工作区、文档关系图、2D/3D 一致性 |
| 制造 | MBOM/Routing/Operation/WorkCenter,EBOM→MBOM 变换 | EBOM/SPBOM 类型、自动重量、normal-bom 自动生成、工单 PDF |
| 合规 | 21 CFR Part 11 电子签名(reason/manifest/不可变审计) | 无(此插件集内) |
| 集成出口 | ERP 发布 outbox(幂等/重试/dry-run) | purchase/sale/project/helpdesk 模块内联 |
| API 规模 | ~13 平台 router + ~146 meta_engine router | Odoo 视图/控制器驱动 |

**一句话**:Yuantus = 广而(局部)浅的企业引擎;OdooPLM = 窄而(经实战)深的 CAD-PDM。

---

## 3. ★ 差距与借鉴清单(核心交付 · 按优先级)

> 图例:🔴 真实缺口(已核验)· 🟡 部分覆盖/需确认 · 🟢 Yuantus 已有(更强/相当)
> "证据"列给出**两侧** `file:line`,正文 §4–§6 逐项展开。

### A. CAD-PDM 核心(最高价值,Yuantus 抽象引擎尚未长出)

| # | 能力 | 状态 | OdooPLM 证据 | Yuantus 现状证据 | 借鉴优先级 |
|---|---|---|---|---|---|
| A1 | **文档↔文档关系图**(装配 HiTree / 2D-3D LyTree / 引用 RfTree / 打包 PkgTree) | 🔴 | `plm/models/ir_attachment_relations.py:28-106`(关系表 + `link_kind`);`ir_attachment.py:366-608`(各类遍历) | 仅有"版本内文件角色"(`version/models.py:155-209` VersionFile role=NATIVE_CAD/DRAWING/GEOMETRY)+ 版本对版本 CAD diff;**无**文档间层级关系 | **P0** |
| A2 | **2D↔3D 时效一致性**(图纸相对模型过期检测 + 保存批次分组) | 🔴 | `ir_attachment.py:1532-1567` `assign_must_update_flag()`;`plm_dbthread.py`(保存批次,避免误判) | grep `must_update / stale(2d/3d) / drawing.*model` 在 `meta_engine/` **无命中**;`cad_diff_router` 只做同一文件版本差异 | **P0** |
| A3 | **工作站级 check-in/check-out**(记录 hostname + PWS 本地路径) | 🔴 | `plm/models/plm_checkout.py:33-53`(`userid`/`hostname`/`hostpws`/`documentid`) | `version/service.py:80-227` 仅 `checked_out_by_id`(用户级);grep `hostname/pws/workstation` 在 version/models **无命中** | **P1**(取决于是否上桌面 CAD 插件) |
| A4 | **pack-and-go 交付包导出**(零件 BOM + 全部 2D/3D/PDF 打包下载) | 🔴 | `plm_pack_and_go/wizard/`(导出类型筛选 + 归档) | 仅有 Baseline **元数据**快照(`models/baseline.py`),无物理多文件打包导出 | **P1** |

### B. 务实 PLM 行为(已核验的真实缺口)

| # | 能力 | 状态 | OdooPLM 证据 | Yuantus 现状证据 | 借鉴优先级 |
|---|---|---|---|---|---|
| B1 | **版本替代信号**:发布新版时把旧版标记 OBSOLETED、改版时把母版标记 UNDER_MODIFY,并锁旧版防并发改版 | 🔴 | `plm_mixin.py:231-252`(`_mark_under_modifie_previous`/`_mark_obsolete_previous`);`299-344`(`new_version`) | `version/service.py:355-410` `revise()` 仅 `current_ver.is_current=False`,旧版仍为 `Released`,**无**显式作废态/在改信号 | **P1** |
| B2 | **装配树发布硬门禁 + 一键级联推进**("已发布父件不得引用未发布子件";一次确认/发布整棵装配) | 🔴(精确化) | `product_product.py:1083-1124` `commonWFAction()`(带状态守卫递归子件)+ `_action_ondocuments` | `LifecycleService.promote()` 仅作用单 Item;release-readiness 仅覆盖 MBOM/Routing/Baseline/ECO 工件(`release_readiness_service.py:79-177`),"子件已发布"只是 baseline **警告**(`release_validation.py:42`),非工程 BOM 硬门禁,也无级联推进 | **P1** |
| B3 | **料号不可变**:`engineering_code` 一经赋值即锁定(与生命周期无关) | 🔴 | `plm_mixin.py:497-517`(write/create 后置 `engineering_code_editable=False`) | `operations/update_op.py:40-42` 仅按生命周期 `is_item_locked` 锁整 Item;Draft 态下料号仍可改 | **P2** |

### C. 待确认 / 部分覆盖(多数已由 Yuantus 以更高级方式实现,建议核验对齐)

| # | 能力 | 状态 | OdooPLM 证据 | Yuantus 现状证据 | 动作 |
|---|---|---|---|---|---|
| C1 | **BOM 比较 → 回写/调和**到目标 BOM | 🟡 | `plm_compare_bom/wizard/compare_bom.py:387`(`action_compare_Bom`)+ `update_bom()` | `services/bom_service.py:66-179`(7 种 compare 模式,只读 diff)+ ECO BOM-change(`models/eco.py` ECOBOMChange) | 确认"经 ECO 把 diff 落地回 BOM"的端到端链路已闭环 |
| C2 | **仅最新已发布版**可被选择(BOM/采购/销售/搜索/ERP 发布各面) | 🟡 | `plm_product_only_latest` / `plm_purchase_only_latest` / `plm_sale_only_latest`(name_search 域过滤) | `services/latest_released_guard.py` + `bom_service.py:732` `assert_latest_released`(仅 BOM 加子件);其余选择/搜索面是否一致需确认 | 把 guard 推广到 search/RPC/ERP 发布 |
| C3 | **时效("date")BOM** + 失效向上传播到 where-used | 🟢/🟡 | `plm_date_bom/models/mrp_bom.py:45,191`(`_obsolete_compute`/`updateWhereUsed`) | 已有 `EffectivityService`(date/lot/serial/unit)+ `bom_obsolete_rollup_router`(`/obsolete` 扫描含 parent + `/obsolete/resolve`) | 核验"按生效日期自动判废 + 递归向上"与 plm_date_bom 对齐 |
| C4 | **按品类序列自动生成工程编码 / 内部料号** | 🟡 | `plm_auto_engcode/models/product_product.py:28`;`plm_auto_internalref/...:44` | 有 `item_number_keys` 别名,但按品类 `ir.sequence` 自动编码偏薄 | 视需要补品类驱动自动编号 |

### D. Yuantus 已有 / Odoo 此插件集无对应(广度优势,标注"深度未审计")

电子签名 21 CFR Part 11(`esign/`)、Baseline 快照+比较+校验(`models/baseline.py`)、有效性 date/lot/serial/unit、可配置工作流引擎(`workflow/`)、通用审批 + 谓词自动化(`approvals/`)、MBOM/Routing/Operation/WorkCenter + EBOM→MBOM 变换(`manufacturing/`)、ERP 发布 outbox(`erp_publication/`)、Release orchestration / cross-domain impact / item cockpit、可插拔版本方案、重量 rollup(已核验**比 Odoo 更强**:支持有效性、write-back 模式、rounding,见 `bom_obsolete_rollup_router.py:180` + `BOMRollupService.compute_weight_rollup`)。
> 这些是**子系统存在性**优势;digest 同时把其中数项标为 partial/stub/bootstrap,**深度请勿默认达标**。

---

## 4. CAD-PDM 核心(A 组)逐项深入

### A1 · 文档↔文档关系图(装配 / 2D-3D / 引用 / 打包)

**OdooPLM 怎么做**
`ir.attachment.relation` 是一张专门的文档关系表(`plm/models/ir_attachment_relations.py:28-106`):
- `parent_id` / `child_id`(均指向 `ir.attachment`)、`link_kind`(Char64)、`configuration`(变体/配置名)
- 唯一约束 `(parent_id, child_id, link_kind)`,并禁止自引用
- `link_kind` 取值是核心领域语义:
  - **HiTree** = 装配层级(父件包含子件,`ir_attachment.py:548-577` `getRelatedHiTree`)
  - **LyTree** = 2D↔3D 配对(图纸与其模型互为对应,`403-444` `getRelatedLyTree`,**双向**:问 3D 给 2D,问 2D 给 3D)
  - **RfTree** = 引用依赖(`501-515`)
  - **PkgTree** = 打包关系(`536-547`)
- 遍历能力:`_explodedocs`(深度递归,`366-382`)、`getRelatedAllLevelDocumentsTree`(`578-608` 整棵树)

**Yuantus 现状**
- 文件归属是"**版本内文件 + 角色**":`version/models.py:155-209` `VersionFile`(role=NATIVE_CAD/PREVIEW/GEOMETRY/DRAWING/ATTACHMENT/REFERENCE、`is_primary`、`snapshot_path`)
- 文档之间**没有**一等关系图;`cad_diff_router` 只做"同一 CAD 文件 vN↔vM"差异
- 关系框架(`relationship/`)是通用的,理论上能用 `is_relationship` 类型建"图纸-of / 引用 / 装配"关系,但**没有种子化的 CAD 文档关系类型**,也没有任何随之而来的语义逻辑

**借鉴建议(P0)**
1. 在通用关系框架上种子化一组**文档关系类型**:`DOC_ASSEMBLY`(=HiTree)、`DOC_2D3D`(=LyTree,双向)、`DOC_REFERENCE`(=RfTree)、`DOC_PACKAGE`(=PkgTree),复用 `RelationshipService`,无需新表。
2. 提供与 OdooPLM 对应的遍历 API:按 link_kind 的一级/递归展开、整棵文档树——对应 `getRelatedOneLevelLinks` / `getRelatedAllLevelDocumentsTree`。
3. 把 A2(时效)建在这层关系之上(2D↔3D 必须先有 LyTree 配对,才能谈"谁比谁新")。

---

### A2 · 2D↔3D 时效一致性(图纸过期检测 + 保存批次分组)

**OdooPLM 怎么做**
- `ir_attachment.py:1532-1567` `assign_must_update_flag()`:在文档上维护布尔标志 `must_update_from_cad`。逻辑——当 3D(或 2D)的 `write_date` 比与之 LyTree 配对的 2D(或 3D)更新时,把落后的一方标记为"需更新"。
- 关键防误判技巧:`plm.cad.open` + **`plm.dbthread`**(`plm/models/plm_dbthread.py`)。一次 CAD 客户端"保存整套装配"会产生很多写操作;若它们属于**同一 dbThread**(同一次保存批次),则**不**互相触发"过期",只有跨批次的真实时间差才触发。这避免了"刚同步保存就满屏标过期"的灾难。

**Yuantus 现状**
- grep `must_update / stale(2d|3d) / drawing.*model / consisten` 在 `meta_engine/` **无命中**(仅有作业层 reclaim-stale 之类无关命中)。
- 有 `cad_change_log` / `cad_history_router` / `cad_review_router`,但都是单文件维度,**没有"图纸相对其模型过期"这一跨文档不变量**。

**借鉴建议(P0,依赖 A1)**
1. 在 `DOC_2D3D` 关系上引入派生标志 `needs_update_from_source`(对应 `must_update_from_cad`),由两端版本/文件的更新时间比较得出。
2. 引入"**保存批次**"概念(对应 `dbThread`):CAD 导入/检入服务一次提交打一个 `import_batch_id`,同批次内的 2D/3D 互不判过期。`cad_import_router` / `cad_checkin_router` 是落点。
3. 暴露"过期图纸"扫描端点,纳入 release-readiness 规则(B2 联动:模型已改但图纸未更新 → 阻止发布)。

---

### A3 · 工作站级 check-in/check-out

**OdooPLM 怎么做**
`plm/models/plm_checkout.py:33-53` 的 `plm.checkout` 记录不仅记"谁",还记"**在哪台机器、哪个本地工作区**":`userid` / `hostname` / `hostpws`(PWS 本地路径) / `documentid`,且每文档唯一(`51-53`)。检出/检入时还会 `_adjustRelations` 把关系上的 `userid` 一并标记(`70-81`)。这对桌面 CAD 插件至关重要——客户端据此判断"这份文件被我自己的另一台机器/另一个工作区占用"。

**Yuantus 现状**
`version/service.py:80-227` 的 checkout/checkin 只有 `checked_out_by_id` + `checked_out_at`(用户级、版本级、文件级三档锁,已经不错),但**不带客户端上下文**(hostname/本地路径)。

**借鉴建议(P1,看是否上桌面 CAD)**
- 若规划桌面 CAD 插件:在 checkout 记录/响应中加入 `client_host` / `client_workspace_path` / `client_info`(JSONB)。Yuantus 已有 `client_info` 模式可复用(电子签名里就存了 `client_ip`/`client_info`,见 `esign/models.py`),把同样的 envelope 用到 checkout 即可。
- 若纯 Web,则此项可降级为"记录来源会话"以便审计,不必做 PWS 路径。

---

### A4 · pack-and-go 交付包导出

**OdooPLM 怎么做**
`plm_pack_and_go/wizard/` 提供向导:选择导出类型(2D/3D/PDF/组合),沿产品/附件树聚合所有相关文档,打包成可下载归档(用 `base64io` 流式处理大包)。典型场景:把一个零件**连同整棵 BOM 的全部图纸/模型/PDF**一次性交给供应商或车间。

**Yuantus 现状**
有 Baseline(`models/baseline.py`)能冻结一棵 BOM 的**元数据**快照(成员、层级、数量),但**没有把对应物理文件打成一个交付包**的能力;File vault / 转换 router 都在,缺的是"按 baseline/BOM 聚合文件 → 归档 → 下载"这条编排。

**借鉴建议(P1)**
- 在 Baseline + File vault 上加一个"**发布包/供应商交付包**"导出:输入 root item + 选项(含 2D/3D/PDF、是否含子件),输出 manifest + 物理归档。天然复用 `BaselineService`(取成员树)+ `file_storage`/`file_conversion`(取/转文件)。
- 与 A1 联动:有了文档关系图,"取齐一个件的全部相关文档"才完整(否则只能取版本主文件)。

---

## 5. 务实 PLM 行为(B 组)逐项深入

### B1 · 版本替代信号(作废旧版 / 在改母版 / 锁防并发改版)

**OdooPLM**:`plm_mixin.py`
- `new_version()`(`299-344`)开新改版时,调用 `_mark_under_modifie_previous()`(`231-245`)把**母版**标为 `undermodify`——UI 上能看到"此版正在被改版"。
- `action_release()` 走 `_mark_obsolete_previous()`(`247-252`),发布新版的同时把**上一版**置 `obsoleted`。
- 唯一约束 `(engineering_code, engineering_revision)`(`113-144`)+ `is_releaseble()` 守卫,保证同码同版唯一、且只有已发布版才能开改版。

**Yuantus**:`version/service.py`
- `revise()`(`355-410`)/`new_generation()`(`412-464`):新建 Draft 版、`is_current=True`,把旧版仅 `is_current=False`。**旧版仍是 `Released`**,母版上**没有**"在改"信号;也未阻止对同一已发布版并发开两个改版分支。

**借鉴建议(P1)**
1. 增加显式终态转移"**Superseded(被取代)**":发布 vN+1 时,把 vN 从 `Released` 推进到 `Superseded`(区别于 `is_current=False` 的纯标志)。这样查询"在役已发布"与"历史已发布"语义清晰。
2. 开改版时给母版打"**Under-Modification**"标记(或由"存在更高代 Draft"派生),供 UI/治理使用。
3. `revise()` 入口加守卫:若已存在未发布的更高 revision,拒绝再次改版(对应 `is_releaseble` 思路)——避免并发改版分叉。

> 注:Yuantus 用 `is_current` + ECO 在更高层管理替代,设计本身更干净;此处借鉴的是**"对外可见的状态信号 + 并发改版守卫"**,不是推翻其模型。

---

### B2 · 装配树发布硬门禁 + 一键级联推进

**OdooPLM**:`product_product.py:1083-1124` `commonWFAction(status, include_statuses, recursive, ...)`
- 沿 BOM 子件**递归**推进状态(confirm/release),用 `_get_recursive_parts(include_statuses)` 找处于匹配态的子件,用 `performed_ids` 防环。
- 同时 `_action_ondocuments()`(`1115-1118`)推进**关联文档**。
- 隐含不变量:发布父件会带动子件一起到位 → **已发布的父件不会挂着 draft 子件**。

**Yuantus**:`LifecycleService.promote()` 只作用**单个 Item**(`lifecycle/service.py`);grep 确认 `lifecycle/` 内无 BOM 子件级联。Release-readiness(`release_readiness_service.py:79-177`)聚合的是 **MBOM / Routing / Baseline / ECO** 工件的发布诊断;"子成员未发布"只在 **baseline** 维度作为**警告**出现(`release_validation.py:42` `baseline.warnings_for_unreleased_or_changed_members`),**不是**对工程 BOM 的硬门禁,也**没有**一键级联推进装配树。

**借鉴建议(P1)**
1. 增加**发布门禁规则**`bom.children_all_released`(硬错误,非警告):发布一个装配件前,校验其当前 BOM 直接/递归子件均已发布。挂到 release-readiness 规则集(已有规则集机制,见 `release_validation.py`)。
2. 提供**级联推进**编排:`promote_assembly(root, target_state)`——按拓扑序对子树批量 promote(复用 `BOMService.get_bom_structure` 取树 + `LifecycleService.promote` 逐点),带防环与"部分失败回滚/报告"。注意与 ECO 的边界:级联推进应在 ECO/release-orchestration 框架内做,保留审计。
3. 文档侧同样校验(对应 `_action_ondocuments`):发布件时其关联文档需到位。

---

### B3 · 料号不可变

**OdooPLM**:`plm_mixin.py:497-517` —— `write()`/`create()` 后置把 `engineering_code_editable=False`,即料号一旦落库即锁,**与生命周期无关**,杜绝发布后(甚至发布前)误改编号。

**Yuantus**:`operations/update_op.py:40-42` 只在 `is_item_locked`(发布/版本锁态)时拒写;**Draft 态下料号可随意改**。

**借鉴建议(P2)**
- 在 `update_op` 加一条属性级不可变规则:`item_number/number` 一经非空赋值即只读(管理员可加显式 override + 审计)。`item_number_keys.ensure_item_number_aliases` 是天然落点。

---

## 6. 待确认项(C 组)——多为"已用更高级方式实现",建议对齐核验

- **C1 BOM compare 回写**:OdooPLM `update_bom()` 直接把差异写回目标 BOM;Yuantus 走 ECO BOM-change(更可审计)。**动作**:核验"compare → 生成 ECOBOMChange → apply → 落回关系 Item"端到端闭环已通(digest 标 ECO conflict resolution 为 stub,需重点验 apply 路径)。
- **C2 仅最新已发布版可选**:Yuantus 已在 BOM 加子件处 `assert_latest_released`(`bom_service.py:732`)并在 BOM 读取处普遍过滤 `is_current`;OdooPLM 还覆盖采购/销售选择面。**动作**:把 `latest_released_guard` 推广到 search/RPC/ERP-publish 选择面,确保各处一致。
- **C3 date-BOM + 向上失效传播**:Yuantus 已有 `EffectivityService` 与 `/bom/{id}/obsolete` 扫描(含 parent)+ `/obsolete/resolve`,大体覆盖甚至更强。**动作**:核验"按生效日期自动判废"与"递归向上 where-used 传播"与 `plm_date_bom`(`updateWhereUsed`)对齐。
- **C4 自动编码**:按需补"品类 → `ir.sequence` 风格自动工程编码/内部料号"。

---

## 7. 建议落地路线(分期)

**Phase 1 — CAD-PDM 关系底座(P0,A1+A2)**
- 种子化文档关系类型(DOC_ASSEMBLY / DOC_2D3D / DOC_REFERENCE / DOC_PACKAGE),复用 `RelationshipService`。
- 在 DOC_2D3D 上做"过期"派生 + 引入 `import_batch_id`(对应 dbThread)防误判;接 `cad_import_router`/`cad_checkin_router`。
- 验收:能查"一个 3D 的全部图纸/引用/装配父子";模型改后其图纸被标过期;同批保存不误标。

**Phase 2 — 发布治理收口(P1,B1+B2)**
- 新增 `Superseded` 转移 + 母版"在改"信号 + 并发改版守卫。
- 新增硬门禁 `bom.children_all_released` + `promote_assembly` 级联推进(ECO/orchestration 内)。
- 把 A2 的"图纸过期"纳入发布门禁。

**Phase 3 — 交付与一致性(P1/P2,A3+A4+B3+C 组)**
- pack-and-go 交付包(Baseline + File vault 编排)。
- 料号不可变规则;checkout 客户端上下文(如上桌面 CAD)。
- C 组逐项核验对齐。

> 每个 Phase 单独取得显式 opt-in 再开工(遵循仓库"逐阶段授权"约定);新增测试务必登记进 `ci.yml` 用例清单,新增 `YUANTUS_*` 配置务必在 Settings 显式声明,避免静默失效。

---

## 8. 附录:关键文件索引(file:line)

**OdooPLM(`/Users/chouhua/Downloads/odooplm-19.0/`)**
- 生命周期/版本/编码核心:`plm/models/plm_mixin.py`(状态常量 `37-58`、转移 `179-289`、改版 `299-344`、替代标记 `231-252`、料号锁 `497-517`、唯一约束 `113-144`)
- 零件工作流递归:`plm/models/product_product.py:1040-1124`(`action_*` + `commonWFAction`)、改版 `1491-1550`
- 文档/CAD:`plm/models/ir_attachment.py`(文档树遍历 `366-608`、改版 `843-897`、检入检出 `1462-1521`、2D/3D 时效 `1532-1567`)
- 文档关系:`plm/models/ir_attachment_relations.py:28-106`
- 检出锁:`plm/models/plm_checkout.py:33-134`;保存批次:`plm/models/plm_dbthread.py`
- BOM:`plm/models/mrp_bom.py`(展开 `346-377`、where-used `270-284`、重量 `747-777`)
- 权限:`plm/security/base_plm_security.xml`、`plm/models/res_groups.py`
- 关键插件:`plm_compare_bom/wizard/compare_bom.py:387`、`plm_web_revision/wizards/product_rev_wizard.py:54`、`plm_pack_and_go/wizard/`、`plm_date_bom/models/mrp_bom.py:45,191`、`plm_product_only_latest/models/product_product.py:30`、`plm_automatic_weight/models/product_product.py:112`、`plm_auto_engcode/models/product_product.py:28`、`plm_workflow_custom_action/models/`、`activity_validation/`

**Yuantus(`/Users/chouhua/Downloads/Github/Yuantus/src/yuantus/`)**
- 版本:`meta_engine/version/service.py`(checkout/checkin `80-227`、revise `355-410`、new_generation `412-464`、release `466-506`)、`version/models.py:155-209`(VersionFile)
- 生命周期:`meta_engine/lifecycle/service.py`、`lifecycle/models.py`
- BOM/关系:`meta_engine/services/bom_service.py`(compare `66-179`、guard `732`)、`relationship/service.py`
- 发布治理:`meta_engine/services/release_readiness_service.py:79-177`、`release_validation.py`、`latest_released_guard.py`
- 重量 rollup:`meta_engine/web/bom_obsolete_rollup_router.py:180`(+ `BOMRollupService.compute_weight_rollup`);失效扫描:`/bom/{id}/obsolete`
- 料号/更新:`meta_engine/operations/update_op.py:40-42`、`services/item_number_keys.py`
- CAD:`meta_engine/web/cad_import_router.py`、`cad_checkin_router.py`、`cad_diff_router.py`、`cad_history_router.py`、`cad_review_router.py`
- 制造/ECO/审批/签名/基线:`meta_engine/{manufacturing,workflow,approvals,esign,models/baseline.py,models/eco.py}`

---

## 9. 复核口径(给评审者)

本报告对"缺口"做了**逐项代码核验**,不少初判被推翻或精确化,记录如下以便信任:
- ❌ "Yuantus 无重量 rollup" → **撤销**:存在且**更强**(有效性/write-back/rounding)。
- ❌ "Yuantus 无最新版守卫" → **撤销**:BOM 加子件处已有 `assert_latest_released`;仅"全选择面一致性"待推广。
- ❌ "Yuantus 无失效向上传播" → **降级为待确认**:`/bom/{id}/obsolete` 扫描已含 parent。
- ✅ "无 2D/3D 时效一致性 / 无文档关系图 / checkout 无工作站上下文 / revise 无显式替代态 / 无工程 BOM 硬门禁与级联 / 料号 Draft 可改" → **均经 grep+读码核验为真**。
- ⚠️ "Yuantus 已领先"清单:仅作**子系统存在性**陈述,**深度未逐行审计**(digest 标多项 partial/stub/bootstrap)。
