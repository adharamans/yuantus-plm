# Claude Taskbook: WP1.0 — CAD-PDM 表征决策(2D/3D · 装配 · 引用)

Date: 2026-06-04

Type: **Doc-only decision taskbook.** 它把"图纸/模型/装配/引用在 Yuantus 里到底表达成什么"这个根基性选择**一次锁死**,据此固定后续 WP1.1/WP1.2/WP1.3 的**实现分支、命名、endpoint 形态**。本 taskbook **不授权写实现代码**,只授权:据此修正 `ODOOPLM_BORROW_DEVELOPMENT_PLAN_AND_TODO_20260604.md` 的措辞,使实现者不再按错误模型动手。合并后,WP1.1/WP1.3 实现才稳。

Origin: `ODOOPLM_19_CADPDM_GAP_AND_BORROW_ANALYSIS_20260604.md`(差距 A1/A2)+ `ODOOPLM_BORROW_DEVELOPMENT_PLAN_AND_TODO_20260604.md`(Phase 1)。审阅中发现:开发计划虽已意识到"不默认 Document graph",但 WP1.1/1.2/1.3 仍残留 `document_relationship_router` / `DOC_2D3D` / `/documents/{doc_id}/staleness` 等**文档中心措辞**——必须先纠正模型,再开实现。

---

## 0. What this is(与诚实结论)

**结论(已据代码核验):** Yuantus 当前的 CAD 持久化模型是 **"Part(Item)+ 按角色挂接的文件(File)"**,**不是** "Document Item + 文档关系图"。因此:

| 能力 | 锁定表征 | 机制 | 是否 P0 默认 |
|---|---|---|---|
| **2D↔3D staleness** | **文件角色比较**(同一 Part 上 `drawing` 文件 vs `native_cad` 文件的时间戳 + 导入/检入批次) | `ItemFile`/`VersionFile.file_role` + `FileContainer.document_type`/`updated_at` | ✅ **默认** |
| **assembly / reference** | **Part↔Part relationship** | `RelationshipService`(关系即 Item,已支持) | ✅ **默认** |
| **Document↔Document graph** | 新建 `Document` Item + 文档间关系 | `RelationshipService`(端点=Document) | ❌ **暂缓,不作 P0 默认** |

这与建议者倾向一致,且**有代码证据支撑**(见 §1)。

---

## 1. Grounding Facts(verified against `main = c6d4dc7f`)

> 全部为读码核验,非推测。引用 `file:line`。

**F1 · CAD 文件 = `FileContainer`(`meta_files`)**
- `models/file.py:81` `class FileContainer`;`document_type`(`:115`)取值由 `DocumentType` 枚举决定:`CAD_3D="3d"`、`CAD_2D="2d"`、`PRESENTATION="pr"`、`OTHER="other"`(`file.py:25-31`)。
- `is_native_cad`(`:118`)、`cad_format`(`:119`,"STEP"/"SOLIDWORKS"...)、`created_at`/`updated_at`(`:161-162`,**带时区**)、还有 `cad_attributes_updated_at`(`:125`)等细粒度时间戳。

**F2 · Part = `Item`(`meta_items`,item_type="Part"),被版本化**
- CAD 导入 `CadImportService.import_file`(`services/cad_import_service.py:498`)→ `_auto_create_or_update_part`(`:621`)按 `item_number` 建/更新一个 **Part** Item(`:654-694`,走 `AMLEngine.apply(type="Part")`)。

**F3 · 文件↔Part 的连接 = 两张"按角色"的连接表(都不是 Document Item)**
- `_attach_to_item`(`:696-751`)把文件以 **`ItemFile`(`meta_item_files`,`file.py:226`)** 挂到 Part:字段 `item_id`+`file_id`+`file_role`(`:744-748`)。
- 版本快照层用 **`VersionFile`(`meta_version_files`,`version/models.py:155`)**:`version_id`+`file_id`+`file_role`(`:179`)+`is_primary`(`:188`)+`snapshot_path`(`:185`);唯一约束 `(version_id, file_id, file_role)`(`:207`)。`VersionFileService` 负责把 item 文件同步进版本快照。
- **`file_role` 枚举(两处一致)**:`native_cad` / `drawing` / `geometry` / `preview` / `attachment` / `reference`(`file.py:34-42` `FileRole`;`version/models.py:29-37` `VersionFileRole`)。**`drawing`=2D 图纸,`native_cad`=3D 模型**——2D/3D 已经能在文件角色层区分。

**F4 · 检入产出 = 新版本 + 转换作业(派生件来自作业,不是文档关系)**
- `CheckinManager.checkin`(`services/checkin_service.py:119`)上传 native 文件→`FileContainer`,`props["native_file"]=id`(`:123`),入队 `cad_preview` + `cad_geometry(glTF)` 作业(`:137-150`),再 `VersionService.checkin(properties=props)`(`:160`)。→ 预览/几何是**转换产物**,挂回同一 Part 版本。

**F5 · `Document` ItemType 存在但 CAD 管线不产出它**
- `seeder/meta/schemas.py:24` 确有 `id="Document"` 的 ItemType(`is_versionable=True`)。但 §F2–F4 全程**只建 Part + 挂文件**,**从不**建 Document Item;Baseline 的 `document_id` 实际指向 `meta_files`(`models/baseline.py:109`),即"文档=文件行"。
- 故"Document↔Document graph"是一个**模型层支持、但当前管线不产生**的第三方案。

**F6 · Part↔Part 关系已完全可用**
- `RelationshipService.create_relationship(source_id, related_id, relationship_type_name, ...)`(`relationship/service.py:43`)对任意 Item 端点工作;BOM 关系类型 `Part BOM` 即同款范式(`seeder/meta/schemas.py:41-50`,`is_relationship=True`)。装配/引用本就是 Part↔Part,**零额外建模**。

---

## 2. 三种表征分支(把选择讲清楚)

- **分支 A — 文件角色比较(within/cross Part version)**:2D 与 3D 是同一/相邻 Part 版本上的两个文件,以 `file_role`(drawing vs native_cad)+ `FileContainer.document_type`(2d vs 3d)+ 时间戳区分。**2D↔3D staleness 用此分支:不需要任何关系图。**
- **分支 B — Part↔Part relationship**:用 `RelationshipService` 在 **Part** 之间建 `ASSEMBLY`/`REFERENCE`(必要时 `DRAWING_OF`)。**装配/引用用此分支。**
- **分支 C — Document↔Document graph**:把图纸/模型建成独立 `Document` Item,再建文档间关系。**暂缓**——与当前管线(F2–F5)不符,贸然引入会制造"双写/双真相"。

---

## 3. DECISION(锁定)

**D1 · 2D↔3D staleness → 分支 A(文件角色比较)。**
判定口径:对同一 Part(当前版本/版本链),取 `file_role=drawing`(或 `FileContainer.document_type="2d"`)的图纸文件与 `file_role=native_cad`(或 `document_type="3d"`)的模型文件;若**模型文件**的 `updated_at`/`cad_attributes_updated_at` 晚于**图纸文件**,且二者**不属于同一导入/检入批次**,则把图纸标"过期"。批次=本 taskbook 引入的 `import_batch_id` 概念(WP1.3 落地)。

**D2 · assembly / reference → 分支 B(Part↔Part relationship)。**
种子化关系 `ItemType`:`ASSEMBLY`(装配父子)、`REFERENCE`(引用),端点均为 `Part`,`is_relationship=True`。复用 `RelationshipService`。

**D3 · Document↔Document graph → 暂缓(非 P0 默认)。**
仅当出现明确"独立图纸件/独立文档对象"业务诉求时,再单独立项评估分支 C。P0 不建 `Document` 关系类型、不建文档中心路由。

**D4 · 命名与 endpoint 形态(锁定;实现者必须照此)**
- 关系遍历服务/路由命名 **用 `pdm_relationship`(或 `cad_relationship`),禁止 `document_relationship`**。
- 关系类型 ID:**`ASSEMBLY` / `REFERENCE`**(Part↔Part);**不引入 `DOC_2D3D` / `DOC_ASSEMBLY` 这类 `DOC_` 前缀类型**(2D↔3D 不是关系类型)。
- Endpoint(以 item/part 为中心,**非** `/documents/...`):
  - `GET /pdm/items/{item_id}/relationships?kind=ASSEMBLY&direction=outgoing`
  - `GET /pdm/items/{item_id}/relationship-tree?kinds=ASSEMBLY,REFERENCE&max_depth=10`
  - `POST /pdm/items/{item_id}/relationships`(body: related_id / kind / properties)
  - `DELETE /pdm/relationships/{relationship_id}`
  - staleness:`GET /cad/items/{item_id}/staleness`、扫描 `GET /cad/items/{root_id}/stale-drawings`(**非** `/documents/{doc_id}/staleness`)。
- 服务方法命名:`PdmRelationshipService.get_relationships / get_relationship_tree`(或在现有 `RelationshipService` 上加 `get_relationship_tree`),**不**用 `get_document_links/get_document_tree`。
- staleness 服务:`CadConsistencyService.compute_staleness(item_id)` / `scan_stale_drawings(root_id)`,**读 `ItemFile`/`VersionFile` + `FileContainer` 时间戳**,不读"文档关系"。

---

## 4. 据此须修正的开发计划措辞(本 taskbook 唯一授权的改动)

对 `ODOOPLM_BORROW_DEVELOPMENT_PLAN_AND_TODO_20260604.md`:

- [ ] WP1.1:关系类型只保留 **`ASSEMBLY`/`REFERENCE`(Part↔Part)**;删除/降级 `DRAWING_OF`、`DOC_2D3D`、`DOC_*` 前缀;测试名 `test_pdm_relationship_types.py`。
- [ ] WP1.2:路由 **`pdm_relationship_router`**(`/pdm/items/{item_id}/relationships*`),服务方法 `get_relationships/get_relationship_tree`;删除 `document_relationship_router`、`/documents/{doc_id}/links`、`get_document_links/get_document_tree`、"Document Item"措辞。
- [ ] WP1.3:staleness **基于 `file_role`(drawing vs native_cad)+ `FileContainer` 时间戳 + `import_batch_id`**;endpoint 改 `/cad/items/{id}/staleness`;删除 `DOC_2D3D` 关系、`/documents/{doc_id}/staleness`、"在关系上写 needs_update"措辞(改为写在 `VersionFile` 元数据或版本 `properties`)。
- [ ] WP3.1 pack-and-go:"取齐一个件的相关文档"= 取该 Part 的 `ItemFile`/`VersionFile`(按 role 过滤 2d/3d/pdf)+ 沿 `ASSEMBLY` 关系递归,**非**"沿 DOC_ 关系"。
- [ ] 概览表/依赖图/TODO 中相应命名同步更正。

> 说明:开发计划 §2 顶部已加"表征事实 + fork + WP1.0 spike",方向正确;本 taskbook 把**裁定结果**钉死,§4 是把残留文档中心措辞清零的清单。

---

## 5. 排期建议(锁定,纳入开发计划)

- **Phase 1 P0 最小闭环顺序**:`WP1.0(本 taskbook,doc-only)` → `WP1.1(Part↔Part 关系类型)` → `WP1.3(2D↔3D staleness,文件角色)` → `WP1.2(遍历 API,pdm 命名)`。
- **P1 顺序:先 B2,后 B1。**
  - **B2(装配发布硬门禁 `bom.children_all_released`)**:本质是 release-validation **规则扩展**(`release_validation.py` 加 `item_release` 族 + 求值器),**爆炸半径小、价值直接**,先做。
  - **B1(Superseded / under-modification / 并发 revise)**:触及 `lifecycle`+`version` **状态语义**,风险高,**晚半拍**;先出一份 **B1 状态转换 taskbook** 锁死 `Released→Superseded` 转移与并发守卫语义,再开实现。
- **pack-and-go(WP3.1)不要太早**:依赖 WP1.1/WP1.2 的关系/遍历语义,排在其后(开发计划已置于 Phase 3 且依赖 WP1.2,保持)。

---

## 6. Explicitly REJECTED

- **以 Document↔Document graph 作 P0 默认**:与当前管线(F2–F5)不符,会引入"Part 文件"与"Document Item"双真相、双写与同步债;无对应业务诉求。**拒绝**,留待独立评估(D3)。
- **为 2D↔3D 建关系类型(`DOC_2D3D`/`DRAWING_OF`)作默认**:2D/3D 已可由 `file_role` 区分(F3),建关系是多余间接层。**拒绝**(仅在 D3 触发时重议)。
- **文档中心命名(`document_relationship_router`/`/documents/...`)**:误导实现者按 Document graph 写。**拒绝**,统一 `pdm`/`cad` + item 中心(D4)。

---

## 7. Non-Goals

- 不在本 taskbook 写任何实现代码或迁移。
- 不改持久化模型(不新增 Document Item 流、不改 `ItemFile`/`VersionFile` 结构)。
- 不决定 B1 的具体状态机(另立 B1 taskbook)。

---

## 8. Reviewer Focus

- 核对 §1 grounding 的 `file:line` 是否如实(F3 两张连接表 + `file_role` 枚举;F5 Document ItemType 未被管线产出)。
- 核对 D1 staleness 口径是否用对时间戳(`FileContainer.updated_at`/`cad_attributes_updated_at`,UTC/带时区)与正确角色(drawing vs native_cad)。
- 核对 D4 命名/endpoint 是否彻底去文档中心(无 `document_relationship`、无 `DOC_` 前缀、无 `/documents/{id}/staleness`)。
- 确认排期 D:B2 先于 B1;pack-and-go 在 WP1.2 之后。

---

## 9. Status

- **Decision:** LOCKED(D1–D4)。
- **Authorizes:** 仅 §4 的开发计划措辞修正(doc-only)。
- **Blocks until merged:** WP1.1/WP1.3 实现。
- **Follow-ups:** B1 状态转换 taskbook;(条件触发)分支 C 评估。
- **DoD 提醒(实现阶段适用):** 新测试入 `ci.yml:244` 清单;新 `YUANTUS_*` 入 `config/settings.py`;新 router `include_router` 进 app;异常 `... from exc`;时间统一 UTC/带时区。
