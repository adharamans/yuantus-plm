# OdooPLM 借鉴项 · 详细开发计划与 TODO

> 生成日期:2026-06-04 · 配套文档:[`ODOOPLM_19_CADPDM_GAP_AND_BORROW_ANALYSIS_20260604.md`](./ODOOPLM_19_CADPDM_GAP_AND_BORROW_ANALYSIS_20260604.md)
> 本文把差距分析中的 7 个真实缺口(A1–A4、B1–B3)+ C 组待确认项,落成**可执行的工作包(WP)、逐文件改动清单、数据模型/API/迁移/种子/设置/测试、验收标准与勾选式 TODO**。
> 范围:`/Users/chouhua/Downloads/Github/Yuantus/src/yuantus/`。每个 Phase 单独取得显式 opt-in 再开工(遵循仓库"逐阶段授权"约定)。

---

## 0. 读法与工作量总览

| WP | 能力(差距编号) | 优先级 | 依赖 | 估算 | Phase |
|---|---|---|---|---|---|
| WP1.0 | 表征决策 ✅ **已裁定**(独立 taskbook;文件角色 + Part↔Part,Document graph 暂缓) | P0 | — | done | 1 |
| WP1.1 | 关系类型种子化:装配/引用=Part↔Part(`ASSEMBLY`/`REFERENCE`)(A1) | P0 | WP1.0 | 1.5d | 1 |
| WP1.2 | 关系遍历 API(A1) | P0 | WP1.1 | 2d | 1 |
| WP1.3 | 2D↔3D 时效(默认文件角色比较)+ 保存批次(A2) | P0 | WP1.0 | 3d | 1 |
| WP2.1 | Superseded 态 + 在改信号 + 并发改版守卫(B1) | P1 | — | 2.5d | 2 |
| WP2.2 | 装配树发布硬门禁规则(B2a) | P1 | — | 2d | 2 |
| WP2.3 | 级联推进 `promote_assembly`(B2b) | P1 | WP2.2 | 2.5d | 2 |
| WP3.1 | pack-and-go 交付包(A4) | P1 | WP1.2 | 3d | 3 |
| WP3.2 | 料号不可变(B3) | P2 | — | 0.5d | 3 |
| WP3.3 | 工作站级 checkout 上下文(A3) | P1* | — | 1.5d | 3 |
| WP3.4 | C 组核验对齐(compare 回写/最新版面/date-bom/自动编码) | P2 | — | 2d | 3 |

\* WP3.3 优先级取决于是否上桌面 CAD 插件;纯 Web 可降级或免做。

**依赖图(关键路径)**:`WP1.0 → {WP1.1, WP1.3}`;`WP1.1 → WP1.2 → WP3.1`;`WP2.2 → WP2.3`;其余相互独立。
**建议节奏**:Phase 1(CAD-PDM 底座:WP1.0✅→1.1→1.3→1.2)→ Phase 2(发布治理:**先 B2/WP2.2,后 B1/WP2.1**;2.3 依赖 2.2)→ Phase 3(交付/一致性)。`pack-and-go(WP3.1)`依赖 WP1.2,勿提前。

---

## 1. ⚠️ 贯穿性"完成定义"(DoD)与静默失败陷阱清单

> 以下每条都是仓库里已经踩过的坑(见 `memory/feedback_yuantus_silent_failure_traps.md`)。**每个 WP 收尾必须逐条核对**,否则新代码会"看似通过、实则没跑/没生效"。

- [ ] **新测试必须登记进 CI**:`.github/workflows/ci.yml` 第 ~244 行 `pytest -q \` 是**显式文件清单**;新 `test_*.py` 不加进去就不会在 CI 跑(本地绿、CI 假绿)。
- [ ] **改动文件配套测试映射**:`ci.yml:118` / `ci.yml:147` 维护"改了哪些文件就必须带哪些测试"的 changed-files gate;新增 router/service 文件需纳入对应分组或带 companion 测试。
- [ ] **新 `YUANTUS_*` 环境变量必须在 `src/yuantus/config/settings.py` 的 `Settings` 里声明字段**(`env_prefix="YUANTUS_"`,`extra="ignore"` 会**静默丢弃**未声明项)。参考既有 `RELEASE_VALIDATION_RULESETS_JSON`(`settings.py:408`)、`LATEST_RELEASED_GUARD_DISABLED`(`:412`)。
- [ ] **注册了但无副作用的扩展 = 静默 no-op**:新 router 必须挂进 app(确认 `include_router`);新生命周期状态/关系类型必须真正种子化并被服务读到。
- [ ] **异常链**:`raise NewError(...) from exc`(仓库已有 `*_exception_chaining` 测试族强制此规范)。
- [ ] **DB 门控测试**:依赖真实 DB 的用例用既有 DB-gated 标记/夹具,避免 SQLite 缺特性导致误跳过。
- [ ] **迁移三套**:模型改动需同步 `migrations/`(主)、必要时 `migrations_tenant/`、`migrations_identity/`;`alembic upgrade head` 可跑通。
- [ ] **租户隔离 / 缓存**:对租户作用域数据,读投影遵循 `auth → is_entitled → query`,响应带 `Cache-Control: no-store` + `Vary`(见 `feedback_plm_collab_entitlement_invariant.md`)。
- [ ] **事件总线**:新增领域动作发 `DomainEvent` 走 `events/transactional.enqueue_event`(与 `update_op.py:73` 一致),勿直接同步调用副作用。

---

## 2. Phase 1 — CAD-PDM 关系底座(A1 + A2)

> 🔬 **开工前必读 · 一个已核验的表征事实(决定 WP1.1/1.3 的形态)**
>
> Yuantus 里"文档/图纸/模型"的表征是**混合的**,务必先认清,否则会按错误分支开发:
> - **存在 `Document` ItemType**(`src/yuantus/seeder/meta/schemas.py:24`,`id="Document"`,`is_versionable=True`)——所以"文档即 Item"在模型层是被支持的。
> - **但当前 CAD 管线并不这样存**:`CadImportService.import_file`(`services/cad_import_service.py:498`)走 `_auto_create_or_update_part`(`:621` 创建/更新一个 **Part** Item)+ `_attach_to_item`(`:597`)把文件作为 **`VersionFile`** 挂到该 Part 版本上(`VersionFileService`,`file_role` ∈ NATIVE_CAD/DRAWING/GEOMETRY/...)。`CheckinManager.checkin`(`services/checkin_service.py:119`)同理:检入即给 Part 建新版本快照 + `FileContainer`。
> - **没有** OdooPLM `linkeddocuments` 式的 Part↔Document 多对多;Baseline 里的 `document_id` 实际指向 `meta_files`(`models/baseline.py:109`),即"文档=文件行"。
>
> **由此,2D↔3D 与装配/引用关系存在三种可选表征(fork)**:
> 1. **文件即角色(within/cross Part version)**:2D 图纸与 3D 模型是同一/不同 Part 版本上的 `VersionFile`(role=DRAWING vs NATIVE_CAD)。→ 2D↔3D 时效是**文件角色间**的时间比较,**不需要**文档关系图。
> 2. **Part↔Part 关系**:图纸件与模型件是两个 **Part** Item,用 `RelationshipService`(已支持 Part↔Part)建 "DRAWING_OF" / 装配 / 引用关系。
> 3. **Document↔Document 关系图**:把图纸/模型建成独立 `Document` Item,再建文档间关系(本来 WP1.1 默认假设的分支——但**与当前管线不符**)。
>
> **装配(HiTree)/引用(RfTree)本质是 Part↔Part**,分支 2 直接成立、零额外建模。**2D↔3D(LyTree)与时效**才是真问题,推荐**默认走分支 1(文件角色比较)**,因为它与现有 import/checkin 管线**完全吻合、摩擦最小**;仅当确有"独立图纸件"业务时才退到分支 2。**WP1.0 已裁定**(独立 taskbook,§见 WP1.0),据此开 WP1.1/1.3。

### WP1.0 · 表征决策 ✅ 已裁定(见独立 taskbook)

**状态**:**LOCKED**。决策已落于 `DEVELOPMENT_WP1_0_CAD_PDM_REPRESENTATION_DECISION_TASKBOOK_20260604.md`(doc-only),并据其证据(读 `cad_import_service.py:498-751` + `checkin_service.py:119-240` + `models/file.py:25-42,226` + `version/models.py:29-37,155-207`)裁定:
- **2D↔3D staleness → 文件角色比较**(同一 Part 上 `file_role=drawing` vs `native_cad`;`FileContainer.document_type` 2d/3d;时间戳 + `import_batch_id`)。
- **装配/引用 → Part↔Part relationship**(`RelationshipService`,关系类型 `ASSEMBLY`/`REFERENCE`)。
- **Document↔Document graph → 暂缓**(`Document` ItemType 存在但 CAD 管线不产出它;贸然引入会双真相)。
- **命名/endpoint 锁定**:`pdm_relationship_router`(`/pdm/items/{id}/relationships*`)、`/cad/items/{id}/staleness`;**禁** `document_relationship_router`、`DOC_*` 关系类型、`/documents/{id}/...`。
**本 WP 产物**:上面那份 taskbook + 本开发计划据其完成的措辞修正(WP1.1/1.2/1.3/3.1 已对齐)。无需再开 spike。

### WP1.1 · 关系类型种子化(A1)— 装配/引用(Part↔Part)

> **WP1.0 已锁定**(见 `DEVELOPMENT_WP1_0_CAD_PDM_REPRESENTATION_DECISION_TASKBOOK_20260604.md`):装配/引用走 **Part↔Part** 关系类型;2D↔3D **不建关系**,走文件角色比较(WP1.3);Document↔Document graph 暂缓。本 WP 只种 `ASSEMBLY`/`REFERENCE`。

**目标**:引入一组关系类型,对齐 OdooPLM 的 `link_kind`(`plm/models/ir_attachment_relations.py:78-79`),**但端点类型依 WP1.0 裁定**:
- `ASSEMBLY`(= HiTree,装配父子)——**Part↔Part**(`source/related = Part`)
- `REFERENCE`(= RfTree,引用依赖)——**Part↔Part**
- ~~`DRAWING_OF` / `DOC_2D3D`(= LyTree,图纸↔模型)~~ —— **WP1.0 已锁定:不建此关系类型**;2D↔3D 走文件角色比较(WP1.3)。仅在未来触发分支 C 时重议
- `PACKAGE`(= PkgTree,打包)——按需,Part↔Part 或 Document↔Document

**设计**:复用"关系即 Item"——每个类型一行 `ItemType`,`is_relationship=True`,`source_item_type_id`/`related_item_type_id` 指向 **`Part`**(装配/引用)或 `Document`(若分支 3),`is_polymorphic=True`。**无需新表**(`relationship/service.py:43-139` `create_relationship` 已支持;BOM 关系类型 `Part BOM` 已是同款范式,见 `seeder/meta/schemas.py:41-50`)。装配/引用本就是 Part↔Part,`RelationshipService` 现成可用,**本 WP 主要是种子化类型 + 约定 `properties`(position/config 等,对齐 Odoo `configuration`)**。

**逐文件改动**
- `src/yuantus/seeder/meta/`(确认路径:**无** `meta_engine/seeder/`)下新增/扩展种子(参照该目录现有 `lifecycles.py`/`schemas.py`/`eco_stages.py` 风格):写入上述 4 个关系 `ItemType`(`id`/`label`/`is_relationship=True`/`source_item_type_id=related_item_type_id=<Document type id>`/`is_polymorphic=True`)。
- `ItemType` 字段参考:`models/meta_schema.py:14-79`(`is_relationship:29`、`source_item_type_id:65`、`related_item_type_id:68`)。
- 关系属性约定:为 `ASSEMBLY` 约定 `position`/`config` 等(对齐 Odoo `configuration`,`ir_attachment_relations.py:77`);`REFERENCE` 约定引用类型。**2D↔3D 不建关系类型**(WP1.0 锁定走文件角色,见 WP1.3)。

**数据/迁移**:仅数据种子,无 schema 变更(如关系类型走静态种子表则需迁移登记)。
**测试**(`meta_engine/tests/test_doc_relationship_types.py`):
- 种子后 4 个 `ItemType` 存在且 `is_relationship=True`;
- `RelationshipService.create_relationship(part_a, part_b, "ASSEMBLY")` 成功且 `source_id/related_id` 正确;
- 类型不匹配(把非 Part 当端点)被 `create_relationship` 拒绝(`service.py:86-98`)。
**验收**:能为两个 Part Item 建立 `ASSEMBLY`/`REFERENCE` 关系并按类型查询。
**DoD 提醒**:种子要被 bootstrap 真正执行(否则 no-op);新测试登记进 `ci.yml`。

---

### WP1.2 · PDM 关系遍历 API(A1)— **item 中心,`pdm` 命名(WP1.0 锁定 D4)**

**目标**:提供 Part↔Part 关系的一级展开、递归展开、整棵关系树(对应 `ir_attachment.py:385-400 getRelatedOneLevelLinks` / `:578-608 getRelatedAllLevelDocumentsTree`,但端点是 **Part/Item**)。

**设计**:在 `RelationshipService`(或新建 `PdmRelationshipService`)上加查询方法(已有 `get_relationships(item_id, direction, relationship_type_name)`,`service.py:141-175`,以及 `get_bom_tree`,`:177-230` 可作递归模板):
- `get_relationships(item_id, kinds, direction)` → 一级关系列表(已大体具备,扩展多 kind 过滤);
- `get_relationship_tree(root_item_id, kinds, max_depth)` → 递归树(防环用 `visited` set,参照 `get_bom_tree` 的 `max_depth`)。
- *(不做 2D↔3D 双向遍历——2D/3D 不是关系,见 WP1.3。)*

**逐文件改动**
- 服务:扩展 `relationship/service.py`(新增 `get_relationship_tree` 等)。
- 路由:新增 `meta_engine/web/pdm_relationship_router.py`(前缀 `/pdm`,参照 `bom_tree_router`/`bom_children_router` 风格;**禁止命名 `document_relationship_router`**),端点(WP1.0 D4):
  - `GET /pdm/items/{item_id}/relationships?kind=ASSEMBLY&direction=outgoing`
  - `GET /pdm/items/{item_id}/relationship-tree?kinds=ASSEMBLY,REFERENCE&max_depth=10`
  - `POST /pdm/items/{item_id}/relationships`(body: related_id/kind/properties)
  - `DELETE /pdm/relationships/{relationship_id}`
- 权限:沿用 `MetaPermissionService.check_permission(item_type_id, AMLAction.get/update, ...)`(参照 `bom_obsolete_rollup_router.py:191-206`)。
- 挂载:把 router `include_router` 进 app(确认 `meta_engine/web/__init__.py` 或 app factory 的注册位置)。

**测试**(`test_pdm_relationship_router.py`):一级/递归查询契约、防环、权限 403、异常链。
**验收**:给定一个装配 Part,能查出其全部子件/引用(一级与整树)。
**DoD**:router 入 app + 入 `ci.yml` changed-files 分组;新测试登记。

---

### WP1.3 · 2D↔3D 时效派生 + 保存批次防误判(A2)

**目标**:实现"图纸相对其模型过期"检测,对齐 `ir_attachment.py:1532-1567 assign_must_update_flag()`,并用"保存批次"避免一次同步保存误标全员过期(对齐 `plm/models/plm_dbthread.py`)。

**设计**(WP1.0 锁定 D1 = **文件角色比较**,与现有 import/checkin 管线吻合;**不涉及任何关系图**)
1. **过期派生**:对**同一 Part**(当前版本/版本链),比较两个文件:`file_role=drawing`(或 `FileContainer.document_type="2d"`)的图纸 vs `file_role=native_cad`(或 `document_type="3d"`)的模型,经 `ItemFile`/`VersionFile`(`file.py:226` / `version/models.py:155`)关联到 `FileContainer`,取其 `updated_at` / `cad_attributes_updated_at`(`file.py:125,162`,带时区)。若**模型**晚于**图纸**且不在同一保存批次 → 把图纸标过期(`needs_update` 标志写在 `VersionFile` 元数据或版本 `properties`,**不写关系**)。
2. **保存批次(对应 dbThread)**:在 CAD 导入/检入引入 `import_batch_id`。一次客户端"保存整套装配"在同一 `import_batch_id` 下提交;同批次内 2D/3D 互不判过期。批次戳在 `FileContainer`/版本元数据。
3. **暴露**(item 中心,WP1.0 D4):`GET /cad/items/{item_id}/staleness`、`GET /cad/items/{root_id}/stale-drawings` 扫描端点(**禁止 `/documents/{doc_id}/staleness`**)。

**逐文件改动**
- 导入入口:`web/cad_import_router.py:99 import_cad` → `services/cad_import_service.py CadImportService.import_file`。在 `CadImportRequest` 增 `import_batch_id: Optional[str]`(默认服务端生成),贯穿到 `FileContainer`/版本/`ItemFile` 元数据。
- 检入入口:`web/cad_checkin_router.py:169 checkin_document` → `services/checkin_service.py CheckinManager.checkin(item_id, content, filename)`。同样接受/生成 `import_batch_id`。
- 新服务:`services/cad_consistency_service.py`,方法 `compute_staleness(item_id)` / `scan_stale_drawings(root_id)`,**读 `ItemFile`/`VersionFile` + `FileContainer` 时间戳 + 批次**(不读关系)。沿装配的向下扫描经 WP1.2 的 `ASSEMBLY` 树。
- 设置:若引入"过期判定时间裕度"等开关,在 `config/settings.py` 声明 `YUANTUS_CAD_STALENESS_*` 字段(否则 `extra="ignore"` 静默丢弃)。
- 路由:新增 `web/cad_consistency_router.py`(`/cad/items/...`)暴露扫描端点;挂进 app。

**测试**(`test_cad_consistency_staleness.py`):
- 模型版本更新后其 2D 图纸被标过期;
- 同 `import_batch_id` 的 2D/3D 同步保存**不**互标过期;
- 跨批次真实时间差才触发;
- root 扫描返回所有过期图纸(含 parent 链路)。
**验收**:改模型→图纸自动过期;同批保存无误标;扫描可用,并可供 WP2.2 发布门禁调用。
**风险**:时间比较需统一时区(全用 UTC,与 `version/service.py` `datetime.utcnow()` 一致);"主文件"判定要稳定(用 `VersionFile.is_primary`)。

---

## 3. Phase 2 — 发布治理收口(B2 + B1)

> 🔁 **执行顺序锁定(审阅意见):先 WP2.2(B2),后 WP2.1(B1)。**
> - **B2(装配发布硬门禁)** 本质是 release-validation **规则扩展**,爆炸半径小、价值直接 → **先做**。
> - **B1(Superseded/在改/并发 revise)** 触及 `lifecycle`+`version` **状态语义**,风险高 → **晚半拍**,且**先出一份 B1 状态转换 taskbook**(锁死 `Released→Superseded` 转移与并发守卫语义)再开实现。
> WP 编号沿用历史(2.1/2.2/2.3),不重排号;仅执行序为 **2.2 → 2.1**(2.3 依赖 2.2)。

### WP2.1 · Superseded 态 + 在改信号 + 并发改版守卫(B1)— ⚠️ 先出 taskbook,后实现

**目标**:对齐 OdooPLM 的版本替代语义(`plm_mixin.py:231-252`、`:299-344`):
- 发布 vN+1 时把 vN 从 `Released` 推进到显式 **`Superseded`**(区别于纯 `is_current=False`);
- 开改版时给母版打"**Under-Modification**"信号;
- 阻止对同一已发布版**并发开两个改版**。

**设计**
1. **新增生命周期状态 `Superseded`**:`LifecycleState` 已有 `is_end_state`/`is_released`/`is_suspended`/`version_lock` 标志(`lifecycle/models.py:43-55`)。`src/yuantus/seeder/meta/lifecycles.py` 已种子化 "Standard Part Lifecycle":Draft → Review → **Released**(`is_released=True, lock=True, seq=30`,`:22`)→ Suspended → Obsolete,经辅助方法 `_ensure_state(lc_map, key, label, is_released, lock, seq)` 与 `_ensure_transition(...)`。**注意已存在 `Obsolete`/`Suspended`**——`Superseded`(被新版取代)语义不同于 `Obsolete`(手动报废)与 `Suspended`(暂停消费),应**新增独立状态**(`is_released=True`、`version_lock=True`、视需要 `is_end_state`),并加 `Released → Superseded` 的 transition。直接复用上述两个辅助方法即可,有现成范式。
2. **revise 时联动**:`version/service.py:355-410 revise()` 当前仅 `current_ver.is_current=False`。改为:在创建新 Draft 版后,对**旧版对应 Item** 触发生命周期推进到 `Superseded`(经 `LifecycleService.promote`,以保留 hooks/审计),或在版本层显式置 `current_ver.state="Superseded"` 并发 `ItemStateChangedEvent`。两者择一,推荐走 `LifecycleService` 以统一审计。
3. **在改信号**:不必新增字段——可由"存在更高 generation/revision 且为 Draft"派生;若要可查询,给母版 `properties` 写 `under_modification=True`,改版发布/取消时清除。
4. **并发守卫**:`revise()` 入口加检查——若该 Item 已存在未发布的更高 revision(查 `ItemVersion` where item_id 且 `is_released=False` 且 revision 更高),抛 `VersionError`(对应 Odoo `is_releaseble` 思路,`plm_mixin.py:295-297`)。

**逐文件改动**
- `seeder/meta/lifecycles.py`:加 `Superseded` 状态 + transition。
- `version/service.py`:`revise()` / `new_generation()` 联动 + 并发守卫;`release()`(`:466-506`)在发布新版后确保旧版转 `Superseded`。
- `lifecycle/service.py`:确认 `promote` 可被版本服务调用(注意不要循环依赖;如有,经事件或 service 注入)。
- 迁移:`Superseded` 是数据种子,无 schema 变更;若加显式字段则补迁移。
**测试**(`test_version_supersede.py`):
- revise 后旧版 state=`Superseded`、`is_current=False`,新版 Draft;
- 母版"在改"信号置位/清除;
- 对已有未发布更高版的 Item 再次 revise 被拒;
- `Superseded` 态 `version_lock` 生效(不可改)。
**验收**:版本替代语义对外可见且可查询;并发改版被守卫。
**注意**:Yuantus 用 `is_current`+ECO 管理替代,本 WP 只补"对外状态信号 + 并发守卫",勿破坏既有 ECO 流。

---

### WP2.2 · 装配树发布硬门禁规则 `bom.children_all_released`(B2a)

**目标**:对齐 OdooPLM 隐含不变量"已发布父件不得引用未发布子件"(`product_product.py:1083-1124 commonWFAction` 的递归后果),把它变成 Yuantus 的**硬门禁规则**(当前只有 baseline 的**警告** `release_validation.py:42`)。

**设计**(完全套用现有 release-validation 机制)
1. **注册规则族**:`services/release_validation.py` 新增 `kind="item_release"`:
   - `ITEM_RELEASE_RULES_DEFAULT = ["item.exists", "item.not_already_released", "bom.children_all_released"]`
   - 加进 `_BUILTIN_RULESETS`(`:81`)、`_ALLOWED_RULE_IDS`(`:98`)、`_EXISTENCE_RULES["item_release"]="item.exists"`(`:106`);提供 `readiness` 变体(去掉 `not_already_released`,对齐既有 `_without` 模式 `:57`)。
2. **实现求值器**:新增 `services/item_release_service.py` 的 `get_release_diagnostics(item_id, *, ruleset_id)`,**严格仿照** `baseline_service.py:494-583` 的 errors/warnings + `ValidationIssue` 结构:
   - `bom.children_all_released`:用 `BOMService.get_bom_structure(item_id, levels=...)`(`services/bom_service.py:219`)展开当前 BOM;对每个子件查其 current 版本是否 `is_released`(或 Item.state ∈ released/approved,参照 baseline 判定 `:566`)。未发布 → `errors.append(ValidationIssue(code="child_not_released", rule_id="bom.children_all_released", details={child_id, child_number, child_state}))`。
   - 可选 `bom.documents_released`:发布件其关联文档需到位(对应 `_action_ondocuments`)。
3. **接入发布**:在 `release_readiness_service.get_item_release_readiness`(`:79-177`)的 `resources` 里追加 `item_release` 诊断;并在实际 promote-to-Released 路径(`LifecycleService.promote` 进入 `is_released` 状态前)调用该诊断,**有 error 即阻止**(目前 readiness 仅汇总展示,需新增"硬阻断"接线)。

**逐文件改动**:`services/release_validation.py`、新建 `services/item_release_service.py`、`services/release_readiness_service.py`(汇总接线)、`lifecycle/service.py`(promote 到 Released 前调用门禁)。
**设置**:可经 `YUANTUS_RELEASE_VALIDATION_RULESETS_JSON`(`settings.py:408`,已存在)切换 ruleset;新增 env 才需在 Settings 声明。
**测试**(`test_item_release_children_gate.py`):
- 子件未发布 → 父件 promote-to-Released 被 error 阻断;
- 子件全发布 → 通过;
- readiness 变体不含 `not_already_released`;
- 未知 rule id 配置被 `get_release_ruleset` 拒绝(`:178-185`)。
**验收**:父件无法在子件未发布时进入 Released。
**风险**:与 ECO release-orchestration 的边界——门禁应作为统一规则,既被 readiness 展示、也被实际发布阻断,避免两套口径。

---

### WP2.3 · 级联推进 `promote_assembly`(B2b)

**目标**:一键把整棵装配按拓扑序推进到目标态(对齐 `commonWFAction` 的递归推进),并对文档联动(对齐 `_action_ondocuments`)。

**设计**
- 新方法 `LifecycleService.promote_assembly(root_id, target_state, *, dry_run=False)`:
  1. `BOMService.get_bom_structure(root_id)` 取树;
  2. **自底向上**拓扑序(叶子先 promote);
  3. 逐点调用现有 `promote()`(复用 hooks/条件/权限/门禁 WP2.2);
  4. 防环(`visited`);
  5. 返回结构化结果:每节点 `{item_id, from, to, ok, error}`,**部分失败可报告**(参照 OdooPLM `performed_ids` 防重 + Yuantus release-orchestration 的结构化结果风格)。
- `dry_run`:只跑门禁/校验不落库,供前端"发布预检"。
- 文档联动:对每个件,经 WP1.2 取其关联文档(发布态校验),需要时一并推进。

**逐文件改动**:`lifecycle/service.py`(新方法)、`web/release_orchestration_router.py`(新增 `POST /release/assembly/{root_id}/promote` 端点,复用既有 orchestration 返回风格)。
**测试**(`test_promote_assembly.py`):3 层装配 dry_run 预检、自底向上顺序、部分失败报告、防环、权限。
**验收**:对一棵装配一键 confirm/release,顺序正确、失败可见、可预检。

---

## 4. Phase 3 — 交付与一致性(A4 + B3 + A3 + C 组)

### WP3.1 · pack-and-go 交付包(A4)

**目标**:把一个件连同其 BOM 的全部 2D/3D/PDF 文件打成可下载归档(对齐 `plm_pack_and_go/wizard/`)。

**设计**:Baseline(取成员树)+ File vault/转换(取/转文件)+ WP1.2(取齐每件的相关文档)→ 归档。
- 新服务 `services/pack_and_go_service.py`:`build_package(root_id, options)`,options 含 `include_2d/3d/pdf`、`include_children`、`baseline_id?`、`as_of_date?`。
  1. 经 `BaselineService` 或 `BOMService.get_bom_structure` 得成员;
  2. 每成员取该 Part 的 `ItemFile`/`VersionFile`(按 role 过滤 2d/3d/pdf 文件)+ 沿 `ASSEMBLY` 关系递归(经 WP1.2);
  3. 经 `file_storage`/`file_conversion`(`web/file_storage_router.py`、`file_conversion_router.py`)取/转文件;
  4. 生成 `manifest.json` + 物理归档(zip;大文件流式)。
- 路由:`web/pack_and_go_router.py`,`POST /pack-and-go`(异步走 `jobs`,返回 job + 下载 URL)。
**测试**(`test_pack_and_go.py`):manifest 完整性、按类型筛选、含/不含子件、空 BOM 边界。
**验收**:导出包含一棵 BOM 全部选定文档 + manifest。
**DoD**:大包用 `jobs` 异步,勿阻塞请求;权限校验。

### WP3.2 · 料号不可变(B3)

**目标**:`item_number`/`number` 一经非空赋值即只读(对齐 `plm_mixin.py:497-517`)。
**设计**:在 `operations/update_op.py:44-47`(已算出 `merged` 与 `alias_value`)后加守卫:
```python
existing_number = get_item_number(item.properties or {})
incoming_number = get_item_number(aml.properties or {})
if existing_number and incoming_number and incoming_number != existing_number:
    if not _has_number_override(self.roles):     # 管理员显式 override + 审计
        raise ValidationError("item_number is immutable once assigned", field="item_number")
```
**逐文件改动**:`operations/update_op.py`;override 角色判定复用 RBAC roles(`self.roles`)。
**测试**(`test_item_number_immutable.py`):Draft 下改料号被拒;首次赋值允许;管理员 override 允许并记审计;别名 `number`/`item_number` 同步(`item_number_keys.ensure_item_number_aliases`)。
**验收**:已赋值料号不可被普通更新改写。

### WP3.3 · 工作站级 checkout 上下文(A3,条件性)

**目标**:checkout 记录携带客户端上下文(对齐 `plm_checkout.py:33-53` 的 `hostname`/`hostpws`)。
**设计**:`CheckinManager.checkout`(`services/checkin_service.py`)与 `version/service.py:80` checkout 增可选 `client_host`/`client_workspace_path`/`client_info(JSONB)`;复用电子签名已有的 `client_ip`/`client_info` envelope 模式(`esign/models.py`)。版本/检出模型加列 → 迁移。
**逐文件改动**:`version/models.py`(加列 + 迁移)、`version/service.py`、`services/checkin_service.py`、`web/cad_checkin_router.py:129 checkout`。
**测试**:checkout 透传客户端上下文并可查询。
**判定**:仅当规划桌面 CAD 插件才做完整版;纯 Web 降级为"记录来源会话"用于审计。

### WP3.4 · C 组核验对齐(P2,多为确认而非新建)

- **C1 BOM compare 回写**:核验 `BOMService` compare(`bom_service.py:66-179`) → `ECOBOMChange`(`models/eco.py`)→ apply 落回关系 Item 的端到端链路(digest 标 ECO conflict resolution 为 stub,**重点验 apply 路径是否闭环**);补缺口测试 `test_bom_compare_apply_roundtrip.py`。
- **C2 最新已发布版选择面一致性**:把 `latest_released_guard`(`services/latest_released_guard.py` + `bom_service.py:732`)推广到 search/RPC/ERP-publish 选择面;补测试断言各面拒绝过期版。
- **C3 date-BOM 对齐**:核验 `EffectivityService` + `/bom/{id}/obsolete`(含 parent)是否覆盖"按生效日期自动判废 + 递归向上传播"(对齐 `plm_date_bom updateWhereUsed`);差什么补什么。
- **C4 自动编码**:按需补"品类 → `ir.sequence` 风格自动工程编码/内部料号"(对齐 `plm_auto_engcode`/`plm_auto_internalref`);落点 `item_number_keys` + Add 操作 hook。

---

## 5. 主 TODO 清单(勾选式)

### Phase 1 — CAD-PDM 底座
- [ ] **WP1.0** 读 `cad_import_service.py:498-660` + `checkin_service.py:119-235` 函数体,裁定表征分支(默认:装配/引用=Part↔Part;2D↔3D=文件角色),把裁定写回本文件
- [ ] **WP1.1** 种子 `ASSEMBLY`/`REFERENCE`(Part↔Part)关系 ItemType(+条件 `DRAWING_OF`),`is_relationship=True`(范式见 `schemas.py:41-50` Part BOM);bootstrap 真正执行;`test_relationship_types_docpdm.py` 入 ci.yml
- [ ] **WP1.2** `RelationshipService.get_relationships/get_relationship_tree`(防环);`pdm_relationship_router.py`(`/pdm/items/{id}/relationships*`,4 端点)挂进 app;`test_pdm_relationship_router.py` 入 ci.yml
- [ ] **WP1.3**(文件角色比较)`import_batch_id` 贯穿 import/checkin;`cad_consistency_service.compute_staleness(item_id)/scan_stale_drawings(root_id)`(比 drawing vs native_cad 文件时间 + 批次);`/cad/items/{id}/staleness` + `stale-drawings` 端点;`YUANTUS_CAD_STALENESS_*`(如有)入 Settings;`test_cad_consistency_staleness.py` 入 ci.yml

### Phase 2 — 发布治理(执行序:2.2 → 2.1;2.3 依赖 2.2)
- [ ] **WP2.2(先做)** `item_release` ruleset + `bom.children_all_released` 规则;`item_release_service.get_release_diagnostics`;接入 readiness + promote 硬阻断;`test_item_release_children_gate.py` 入 ci.yml
- [ ] **WP2.3** `LifecycleService.promote_assembly`(自底向上 + dry_run + 部分失败报告 + 防环);`POST /release/assembly/{root_id}/promote`;`test_promote_assembly.py` 入 ci.yml
- [ ] **WP2.1(晚半拍,先出 taskbook)** 先写 `DEVELOPMENT_B1_VERSION_SUPERSEDE_STATE_TASKBOOK`(锁状态转换语义)→ 再种 `Superseded` 态 + transition;`revise/new_generation/release` 联动替代 + 在改信号 + 并发改版守卫;`test_version_supersede.py` 入 ci.yml

### Phase 3 — 交付与一致性
- [ ] **WP3.1** `pack_and_go_service.build_package` + `POST /pack-and-go`(异步 jobs + manifest + zip);`test_pack_and_go.py` 入 ci.yml
- [ ] **WP3.2** `update_op` 料号不可变守卫 + 管理员 override + 审计;`test_item_number_immutable.py` 入 ci.yml
- [ ] **WP3.3**(条件)checkout 客户端上下文列 + 迁移 + 透传;测试
- [ ] **WP3.4** C 组核验:compare 回写闭环 / 最新版面推广 / date-bom 对齐 / 自动编码;各补测试

### 每个 WP 收尾必过(DoD,见 §1)
- [ ] 新测试已加入 `ci.yml:244` 显式清单;改动文件纳入 `ci.yml:118/147` 映射
- [ ] 新 `YUANTUS_*` 已在 `config/settings.py` 声明
- [ ] 新 router 已 `include_router` 进 app;新状态/关系类型已被服务读到(非 no-op)
- [ ] 异常 `... from exc`;迁移 `alembic upgrade head` 通过;UTC 时间;事件经 `enqueue_event`

---

## 6. 测试矩阵(关键断言)

| WP | 测试文件 | 核心断言 |
|---|---|---|
| 1.1 | test_relationship_types_docpdm.py | ASSEMBLY/REFERENCE(+条件 DRAWING_OF)存在/is_relationship;Part↔Part 建/拒关系 |
| 1.2 | test_pdm_relationship_router.py | 一级/递归;防环;权限 403;异常链 |
| 1.3 | test_cad_consistency_staleness.py | 改模型→图纸过期;同批不误标;跨批触发;root 扫描 |
| 2.1 | test_version_supersede.py | 旧版 Superseded;在改信号;并发改版拒绝;version_lock |
| 2.2 | test_item_release_children_gate.py | 子件未发布阻断;全发布通过;readiness 变体;未知 rule 拒绝 |
| 2.3 | test_promote_assembly.py | 自底向上;dry_run;部分失败报告;防环;权限 |
| 3.1 | test_pack_and_go.py | manifest 完整;类型筛选;含/不含子件;空 BOM |
| 3.2 | test_item_number_immutable.py | Draft 改拒绝;首赋允许;override+审计;别名同步 |
| 3.4 | test_bom_compare_apply_roundtrip.py 等 | compare→ECO→apply 闭环;各选择面拒过期版 |

---

## 7. 附录:扩展点速查(精确签名 / file:line)

**关系框架**
- `relationship/service.py:43` `create_relationship(source_id, related_id, relationship_type_name, properties?, user_id?)`
- `relationship/service.py:141` `get_relationships(item_id, direction, relationship_type_name?)`;`:177` `get_bom_tree(part_id, max_depth)`(递归模板)
- `models/meta_schema.py:14` `ItemType`(`is_relationship:29`、`source_item_type_id:65`、`related_item_type_id:68`、`is_polymorphic`)

**版本 / 生命周期**
- `version/service.py:355` `revise()`、`:412` `new_generation()`、`:466` `release()`、`:80-227` checkout/checkin
- `version/models.py:155-209` `VersionFile`(role/is_primary/snapshot_path/checked_out_by_id)
- `lifecycle/models.py:29` `LifecycleState`(`is_released:47`/`is_end_state:46`/`is_suspended:48`/`version_lock:53`);`:81` `LifecycleTransition`
- `lifecycle/service.py` `LifecycleService.promote(...)`(hooks/条件/权限)

**发布校验**
- `services/release_validation.py:81` `_BUILTIN_RULESETS`、`:98` `_ALLOWED_RULE_IDS`、`:106` `_EXISTENCE_RULES`、`:159` `get_release_ruleset(kind, ruleset_id)`
- `services/baseline_service.py:494-583` `get_release_diagnostics`(errors/warnings + `ValidationIssue` 范式)
- `services/release_readiness_service.py:79-177` `get_item_release_readiness`(汇总)
- `services/latest_released_guard.py` + `services/bom_service.py:732` `assert_latest_released`

**BOM / 重量 / 失效**
- `services/bom_service.py:219` `get_bom_structure(...)`、compare `:66-179`
- `web/bom_obsolete_rollup_router.py:180` 重量 rollup;`/bom/{id}/obsolete` 失效扫描(含 parent)

**CAD 导入 / 检入**
- `web/cad_import_router.py:99` `import_cad` → `services/cad_import_service.py CadImportService.import_file`
- `web/cad_checkin_router.py:129/169` checkout/checkin → `services/checkin_service.py CheckinManager`

**更新 / 料号 / 事件 / 设置**
- `operations/update_op.py:21` `execute`(料号守卫落点 `:44-47`);`services/item_number_keys.py`
- `events/transactional.enqueue_event`(`update_op.py:73` 范式)
- `config/settings.py:9` `Settings`(`env_prefix="YUANTUS_"`,`extra="ignore"`;`:408` RELEASE_VALIDATION_RULESETS_JSON;`:412` LATEST_RELEASED_GUARD_DISABLED)

**OdooPLM 对照(参考实现)**
- `plm/models/plm_mixin.py`:替代标记 `231-252`、改版 `299-344`、料号锁 `497-517`
- `plm/models/product_product.py:1083-1124` `commonWFAction`(递归发布)
- `plm/models/ir_attachment.py`:文档树 `366-608`、2D/3D 时效 `1532-1567`
- `plm/models/ir_attachment_relations.py:28-106`(link_kind);`plm/models/plm_checkout.py:33-53`;`plm/models/plm_dbthread.py`
- `plm_pack_and_go/wizard/`;`plm_compare_bom/wizard/compare_bom.py:387`;`plm_date_bom/models/mrp_bom.py:45,191`
