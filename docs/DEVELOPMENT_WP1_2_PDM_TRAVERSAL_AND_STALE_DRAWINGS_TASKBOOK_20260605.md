# Development Taskbook: WP1.2 — PDM 关系遍历 API + stale-drawings 薄封装(契约锁定)

Date: 2026-06-05

Type: **Doc-only decision taskbook(thin).** WP1.2 会**固化 PDM 树契约**——pack-and-go、stale-drawings、BOM/PDM UI 都将消费它,改起来贵。所以先把遍历 API 与 stale-drawings 契约一次锁死,**不写实现码**。授权:据此开 (a) WP1.2 traversal 实现 PR,(b) stale-drawings thin-slice 实现 PR(在 traversal 之后)。

Origin: WP1.0 决策 taskbook(#718,D4 命名 + D6 排期)+ `ODOOPLM_BORROW_DEVELOPMENT_PLAN_AND_TODO`(WP1.2)+ WP1.3 实现(#725,`stale-drawings` 扫描在此被显式推迟到"WP1.2 之后")。

---

## 0. What this is(与一句话契约)

提供**装配树遍历**(沿 `ASSEMBLY` 的 Part↔Part 树)+ 一个**只读的 stale-drawings 扫描**(遍历 × 复用 WP1.3 已物化的 `needs_update`,**不重算 provenance**)。命名沿用 WP1.0 D4 的两个前缀:关系遍历在 `/pdm/items/...`,CAD 时效在 `/cad/items/...`。**不碰 staleness core,不新建 staleness 计算路径。**

---

## 1. Grounding Facts(verified against `main = 203cc84c`)

**F1 · 现有遍历模板有 max_depth、但无 cycle guard、无 dedup**
- `src/yuantus/meta_engine/relationship/service.py:141` `get_relationships(item_id, direction, relationship_type_name)`——过滤 `Item.is_current==True`(`:160`),返回关系 Item(边)。
- `src/yuantus/meta_engine/relationship/service.py:177` `get_bom_tree(part_id, max_depth=10)` → `:192` `_build_tree(...)`:**只有 `current_depth >= max_depth` 守卫(`:211`),没有 `visited` 集**。⇒ 遇环会一路递归到 max_depth(产出膨胀的重复子树),且同一件经多路径会重复出现。WP1.2 必须补**真正的环检测**。

**F1b · 关系边本身就是 `Item`,必须避免字段歧义**
- `src/yuantus/meta_engine/models/item.py` 的关系边使用 `Item.source_id` / `Item.related_id`;WP1.1 种下的 `ItemType(is_relationship=True)` 约束 source/related 都是 `Part`。
- 因此响应契约必须显式区分 `item_id`(件)与 `relationship_id`(边),不能只写 `id`。否则实现/前端很容易把边 id 当件 id,或反过来。

**F2 · WP1.3 staleness 是只读可复用的**
- `src/yuantus/meta_engine/services/cad_consistency_service.py:218` `get_staleness(item_id)` **只读** `ItemFile.needs_update`(物化值),**不调用 `recompute`**;`recompute` 只在 import/checkin + 显式 `POST /cad/items/{id}/staleness/recompute` 触发。⇒ stale-drawings 扫描可直接复用 `get_staleness`,零 provenance 重算。`ItemFile.needs_update` 有索引。

**F3 · WP1.0 D4 已锁两个前缀**
- 关系:`/pdm/items/{id}/relationships*`;CAD 时效:`/cad/items/{id}/staleness`。**禁** `document_relationship`、`DOC_*`、`/documents/...`。WP1.2 必须沿用,不新造 `/pdm/cad/` 混合前缀。

**F4 · WP1.1 关系类型**:`ASSEMBLY`(装配父子,containment)、`REFERENCE`(引用/依赖,非 containment),均 Part↔Part(`is_relationship=True`)。

**F5 · 路由计数有 4 个 pin(CI fan-out 陷阱)**
- 当前 `len(app.routes) == 701`。**4 处全局 pin 必须同步**(WP1.3 踩过):`test_metrics_router_route_count_delta.py`(`EXPECTED_TOTAL_ROUTES`)、`test_phase4_search_closeout_contracts.py`(权威 pin)、`test_breakage_design_loopback_metrics.py`(次级 pin)、`test_tier_b_3_breakage_design_loopback_portfolio_contract.py`(**元契约**,断言字面量 `"len(app.routes) == N"` 存在)。漏一个 contracts 就红。
- 新测试须进 `.github/workflows/ci.yml` contracts 清单(排序)+ `conftest.py` no-DB allowlist。

**F6 · 既有 BOM/CAD 路由的错误与权限模式**
- `src/yuantus/meta_engine/web/bom_tree_router.py:153-157` 先查 root:不存在 404,非 `Part` 400;之后走 `MetaPermissionService.check_permission(... AMLAction.get ...)`。
- `src/yuantus/meta_engine/web/cad_consistency_router.py:47-70` 已注册 `/cad/items/{item_id}/staleness` 与 `/cad/items/{item_id}/staleness/recompute`,读取用 `AMLAction.get`,重算用 `AMLAction.update`。
- WP1.2 新路由应镜像这些模式:root 缺失 404、root 非 Part 400、读接口只要 `AMLAction.get`,异常 `... from exc`。

---

## 2. DECISIONS(锁定 D1–D7)

**D1 · 遍历哪种关系:树只走 `ASSEMBLY`(containment);`REFERENCE` 不进树。**
- 树(递归)默认且 v0 仅允许 `kinds=["ASSEMBLY"]`;若请求 `REFERENCE` 或未知 kind 进 tree,返回 **422**。不要静默忽略,也不要把 `REFERENCE` 跟进树(它是 cross-link,跟进去会把树变成图,污染 pack-and-go / stale 语义)。
- `REFERENCE` 经**一级** `relationships?kind=REFERENCE`(或 flat 列表)暴露,不做递归。
- stale-drawings 沿 `ASSEMBLY` 树走;不跟 `REFERENCE`。

**D2 · tree vs flat:两者都要,语义分明。**
- **tree**:嵌套 containment 投影。**保留共享件重复**(同一件出现在两个子装配 = 结构上各算一次,正确)。每边带 `via_relationship`、`depth`、`path`。
- **flat**:**按件去重**的唯一件集合,带 `occurrence_count` + `first_path` + `first_relationship_path`(给 pack-and-go 取文件、stale 扫描去重用)。
- **root 包含在 tree 与 flat 中**:tree 的 root 是 `depth=0` 且 `via_relationship=null`;flat 的 root 是 `occurrence_count=1`,`min_depth=0`,`first_path=[root_id]`。stale-drawings 必须扫 root 自己以及所有装配子件,避免漏掉总装自身图纸。
- stale-drawings 用 **flat**(唯一件)避免同件被扫 N 次。

**D3 · max_depth:默认 10,硬上限 50,不允许无界。**
- 默认 `max_depth=10`(对齐现有 `get_bom_tree`);超过硬上限 `50` → **422**(不静默 clamp,显式拒绝)。**不支持 `-1`/无界**(防 runaway)。

**D4 · cycle guard + 重复件策略:path-based(祖先环)检测。**
- 用**当前路径(从 root 到当前节点的祖先链)**判环:若某节点已在其祖先链中 → 标 `cycle=true`,**停止下钻**(不再递归该分支),但仍把该节点作为叶子列出。
- **不是**全局 visited:同一件出现在**不同分支**(diamond/共享件)是合法的,tree 保留;只有**祖先重现**才算环。
- flat 去重时,环节点不重复计入。

**D5 · endpoint 命名(沿用 WP1.0 D4 两前缀,锁定):**
- `GET /pdm/items/{item_id}/relationships?kind=&direction=outgoing|incoming|both` —— 一级关系(任意 kind,含 REFERENCE)。
- `GET /pdm/items/{item_id}/relationship-tree?kinds=ASSEMBLY&max_depth=10&projection=tree|flat` —— 递归(默认 ASSEMBLY/tree)。`projection=flat` 是**查询参数,不是新路由**。
- `GET /cad/items/{root_id}/stale-drawings?max_depth=10` —— 沿装配树扫过期图纸。
- **禁** `/pdm/cad/...` 混合前缀、`/documents/...`、`DOC_*`。
- 服务:`PdmRelationshipService`(或扩展 `RelationshipService`)`get_relationship_tree(root, kinds, max_depth, projection)`;`CadStaleDrawingsService` 复用 traversal × `CadConsistencyService.get_staleness`。

**D6 · stale-drawings 复用 `needs_update`,零 provenance 重算。**
- 扫描 = `relationship-tree(flat, ASSEMBLY)` 的唯一件 × 对每件 `CadConsistencyService.get_staleness(part_id)`(**只读**)→ 收集 `needs_update=True` 的图纸,带 `part_id` / `path` / `staleness_reason`。
- 实现可用语义等价的批量只读查询替代逐件 `get_staleness`(避免大装配 N+1):`ItemFile JOIN FileContainer WHERE item_id IN (flat 件集) AND needs_update=True AND document_type='2d' AND file_role IN ('drawing','native_cad')`。这仍只读、零 provenance 重算,响应契约不变。
- v0 **不提供 `recompute=true`**。若调用者需要刷新 verdict,必须先显式调用现有 `POST /cad/items/{item_id}/staleness/recompute`(单件)或后续专门的批量重算 slice;`stale-drawings` 永远不改 provenance、不更新 `staleness_checked_at`。
- **不改 staleness core**(provenance/pin/verdict 逻辑全部不动)。

**D7 · 路由计数 + CI fan-out(精确,防 WP1.3 重蹈)。**
- 本 slice 新增 **3 个 GET 路由**:`relationships`、`relationship-tree`、`stale-drawings` → `701 + 3 = 704`。
- **4 个 pin 全部 701→704 + 元契约字面量改 "== 704"**(F5)。
- 新测试入 ci.yml contracts 清单(排序)+ conftest no-DB allowlist;新 router `include_router` 进 app(`src/yuantus/api/app.py`)。
- 实现分两 PR(见 §5),**route-count 增量按"最终落地的路由数"一次性对齐**——若分两 PR,第一 PR(traversal,+2)对齐到 703,第二 PR(stale-drawings,+1)对齐到 704;每个 PR 都跑完整 contracts 清单本地校验(305 文件)再推。

---

## 3. 响应契约(锁定字段)

**relationships** 行:
```
{ relationship_id, relationship_kind, source_id, related_id,
  counterpart_item_id, counterpart_direction, counterpart_item_type_id,
  counterpart_item_number, counterpart_name, properties }
```
- `direction=outgoing`:counterpart 是 `related_id`;`incoming`:counterpart 是 `source_id`;`both`:逐行显式标 `counterpart_direction`。

**relationship-tree(projection=tree)** 节点:
```
{ item_id, item_type_id, item_number, name,
  depth, path: [item_id...], relationship_path: [relationship_id...],
  cycle: bool,
  via_relationship: null | {
    relationship_id, relationship_kind, source_id, related_id,
    quantity, uom, position, properties
  },
  children: [...] }
```
**relationship-tree(projection=flat)** 元素:
```
{ item_id, item_type_id, item_number, name,
  occurrence_count, min_depth,
  first_path: [item_id...], first_relationship_path: [relationship_id...] }
```
**stale-drawings** 响应:
```
{ root_id, scanned_parts, stale_count,
  drawings: [ { part_id, part_number,
                path: [item_id...], relationship_path: [relationship_id...],
                drawing_file_id, file_role,
                needs_update: true, staleness_reason,
                source_batch_id, import_batch_id } ] }
```
- 权限:root 不存在 404、root 非 `Part` 400;沿用 `MetaPermissionService.check_permission(item_type_id, AMLAction.get, ...)`(参照 `bom_tree_router`/`cad_consistency_router`);异常 `... from exc`。

---

## 4. 排期(锁定,3 步)

1. **本 taskbook(doc-only)** —— 锁 D1–D7 契约。
2. **WP1.2 traversal 实现 PR** —— `get_relationship_tree`(tree+flat,cycle guard,max_depth cap)+ `relationships`/`relationship-tree` 两 endpoint + tests;route-count 4 pin → 703。
3. **stale-drawings thin-slice PR** —— `stale-drawings` endpoint = traversal(flat)× `get_staleness`(只读);route-count 4 pin → 704。**不碰 staleness core。**

---

## 5. 测试矩阵(锁定)

| 区 | 用例 |
|---|---|
| relationships | outgoing/incoming/both;`kind=ASSEMBLY`/`kind=REFERENCE`;counterpart 字段不把边 id 与件 id 混淆 |
| tree | 多层装配 tree 投影;`via_relationship`/path/depth 正确;只走 ASSEMBLY;请求 REFERENCE 进 tree → 422 |
| cycle | A→B→A 环:标 `cycle=true`、停止下钻、不无限/不到 max_depth 膨胀 |
| 共享件 | diamond(同件在两分支):tree **保留两份**;flat **去重为一**(occurrence_count=2) |
| depth | `max_depth` 截断正确;超硬上限 50 → 422 |
| flat | 包含 root;去重 + occurrence_count + first_path + first_relationship_path |
| stale-drawings | 扫 root+子件;沿 flat tree 收集 `needs_update=True`;路径正确 |
| stale-drawings 只读 | 不声明 `recompute` 参数;即使调用者传入额外 `recompute=true`,调用前后 `staleness_checked_at` 仍不变(证明没重算) |
| 权限 403 / 异常链 | 通过 |
| route-count | 4 pin 对齐;`len(app.routes)` 增量正确 |
| CI fan-out | 新测试在 ci.yml 清单(排序)+ conftest allowlist |

---

## 6. Explicitly REJECTED

- **`REFERENCE` 进装配树**:把树变图,污染 pack-and-go / stale 语义(D1)。
- **全局 visited 当 cycle guard**:会把合法共享件(diamond)误当环丢掉;必须 path-based(D4)。
- **无界 max_depth(`-1`)**:runaway 风险(D3)。
- **stale-drawings 里重算 provenance / 改 staleness core / 批量 recompute**:违背"薄封装、复用 needs_update"(D6)。
- **`/pdm/cad/` 混合前缀 / `/documents/...` / `DOC_*`**:违反 WP1.0 D4(D5)。
- **只更一个 route-count pin**:WP1.3 踩过,4 个 pin + 元契约缺一即红(D7/F5)。

---

## 7. Non-Goals

- 不写实现码/迁移(本 taskbook 仅 doc)。
- 不做关系**写**端点(POST/DELETE relationships)——本 slice 只读遍历;写另议。
- 不做 pack-and-go(WP3.1,依赖本 flat 投影,后续)。
- 不改 WP1.1 关系类型、不改 WP1.3 staleness core。
- 不做 version-switch 后自动重算(WP1.3 已记的独立 follow-up)。

---

## 8. Reviewer Focus

- D1:树只走 ASSEMBLY、REFERENCE 不进树 —— 认可?
- D4:path-based 环检测(祖先重现才算环,共享件保留)—— 认可?
- D5:沿用 `/pdm/items/...` + `/cad/items/...` 两前缀,不新造 `/pdm/cad/` —— 认可?
- D2/§3:响应字段显式区分 `item_id` 与 `relationship_id`,flat 包含 root —— 认可?
- D6:stale-drawings v0 完全只读复用 `needs_update`,不提供树级 recompute —— 认可?
- D7:+3 路由 → 704,4 个 route-count pin + 元契约字面量全改 —— 是否同意分两 PR(703 → 704)?

---

## 9. Status

- **Decision:** LOCKED(D1–D7),待 reviewer 确认。
- **Authorizes:** WP1.2 traversal 实现 PR;随后 stale-drawings thin-slice PR。
- **DoD 提醒(实现阶段)**:4 个 route-count pin + 元契约字面量;ci.yml + conftest 双登记;新 router 进 app;异常 `... from exc`;本地跑完整 contracts 清单(305 文件)再推;DEV/V 记录入 `DELIVERY_DOC_INDEX.md`。
- **Follow-ups:** pack-and-go(WP3.1,用 flat 投影);version-switch 自动重算;B2→B1。
