# CAD 物料助手 v1 — Phase 2 任务书：assistant resolve/create + 字段相似评分

- Date: 2026-06-04
- Status: Task (doc-only; 实现需单独开工授权)
- Parent plan: `docs/DEVELOPMENT_CAD_MATERIAL_ASSISTANT_V1_PLAN_20260603.md` (#701)
- Predecessor: Phase 1 `docs/DEVELOPMENT_CAD_MATERIAL_ASSISTANT_V1_PHASE1_SERVICE_EXTRACTION_20260603.md` (#702 / impl #704，已并入 main `262b38dd`)
- Phase: 2 / N

## 1. 目标

在 Phase 1 抽出的共享 service 之上，新增 **assistant 编排接口**（`resolve` 只读 + `create` 写）与**字段相似评分**。比 Phase 1 风险高：引入新路由、新写路径、评分逻辑 —— 故先 doc-lock。

## 2. 非目标（本阶段明确不做）

- 不做 NL/LLM；provider 仍默认 `none`（env `YUANTUS_AI_PROVIDER`，见 #701 §3.5）。
- 不做 dedup_vision 图纸相似（见 §9，flag 后置，不进必达范围）。
- 不动 Helper Bridge / AutoCAD 客户端（留 Phase 3）。
- 不动 `/sync/*`、`/compose`、`/validate` 现有路由行为。
- 不自动发布物料、不改 BOM。

## 3. 七条硬约束（本任务书锁定）

1. **`resolve` 必须只读** —— 验收加"数据库业务表零写入"断言（见 §6.1）。
2. **`create` 必须复用 `_apply_item_create`** —— 创建后**按 id 回读 Item** 再组装返回（见 §5.2）。
3. **字段相似评分单独成 service** —— `cad_material_similarity_service.py`，不塞回 plugin route，也不塞回 `cad_material_sync_service`（见 §4）。
4. **输入归一** —— `category`→`material_category`、`finish_standard` companion 与 `finish` 主字段归一，避免 key 错配被"空字段不计入分母"静默吞权重（见 §4.2）。
5. **量纲数值感知优先** —— 量纲字段从 compose `template` 引用发现；数值感知比较优先于对合成 `specification` 的 token overlap（见 §4.3）。
6. **Draft 口径按 lifecycle start state 校验** —— 不把字符串 `state="New"` 当 Draft（见 §5.3）。
7. **dedup_vision flag 后置** —— 不进 Phase 2 必达范围，避免文件上传链路拖住主线（见 §9）。

## 4. 字段相似评分 service（约束 3/4/5）

落点：`src/yuantus/meta_engine/services/cad_material_similarity_service.py`（与 `cad_material_sync_service.py` 同目录）。纯函数，输入 `(候选集 / 目标 properties / profile)`，输出打分候选；**不**做 DB 写、不做 profile 解析。

### 4.1 与现有 match 的分工
- 精确/高置信命中仍由 `cad_material_sync_service._find_matching_items` / `_match_strategies`（已抽出）负责。
- 本 service 只做**模糊相似排序**，对"非精确命中"的候选集打分。
- 候选集来源（**护栏锁定，SQL 细节留实现期**；取数与评分分离）：
  - 只查 Item / 业务表，**只读**；
  - 按 `profile.item_type` 限定 item_type；
  - 优先用 `material_category` + `material` 作宽松 anchor；
  - **排除已在 exact match 命中的 ids**；
  - 候选上限（如 200）；
  - 稳定排序 `updated_at desc, created_at desc` 后，再交给 scorer 取 top 10。

### 4.2 字段与输入归一（约束 4）
- 参评 property key：`material_category`、`material`、`name`、`finish`、`heat_treatment`、`description` + profile 量纲字段。
- **归一**：入参 `category`/`material_profile` → `material_category`；表面处理统一到主字段 `finish`（`finish_standard` 作为 companion 合并，不另立权重）。
- **防静默丢权重**：评分前对"参评 key 是否在 profile/候选里取得到非空值"做一次自检；某 key 长期全空 → 告警（呼应 #701 §3.3.5）。

### 4.3 权重与比较（约束 5）
- 权重（合计 1.00，按双方都有值的字段重归一）：`material_category` 0.18 / `material` 0.22 / 量纲 0.30 / `name` 0.10 / `finish` 0.10 / `heat_treatment` 0.05 / `description` 0.05。
- 枚举字段（`material_category`/`finish`/`heat_treatment`）：规范化精确相等=1，否则 0；`material` 牌号允许前缀/token 部分分。
- 自由文本（`name`/`description`）：规范化 token 重叠（Jaccard）。
- **量纲**：字段从 compose `template` 引用发现（`type=number` 直接数值；`blank_size` 等尺寸串 `type=string` 先正则抽数值再比较）；单维相对误差 `<= tol`（默认 0.02）记 1，超出线性衰减；**数值感知优先于** 对 `specification` 的 token overlap（仅当拆分量纲取不到时，才回退正则抽 `specification` → 再不行才 token overlap）。
- 输出：`score 0..1`；`>=0.75` 候选、`>=0.90` 高相似；top 10，排序 `score desc, updated_at desc, created_at desc`（Item 时间列 `updated_at`/`created_at`）。每候选附 `field_contributions`。

## 5. assistant endpoint（约束 1/2/6）

落点：plugin 路由 `/plugins/cad-material-sync/assistant/*`（沿用现 prefix），handler 只编排 service，不重写策略。

### 5.1 `POST .../assistant/resolve`（只读，约束 1）
- 输入：`profile_id`、`cad_fields`、可选 `values`。
- 流程（**CAD 字段必须先映射**，同现有 `/sync/inbound`：先映射再叠加 values）：
  1. `load_profiles` → 解析 profile；
  2. `incoming = cad_fields_to_properties(profile, req.cad_fields or {})`（`main.py:1405`；不写这步 CAD 字段就不参与 compose/match）；
  3. `incoming.update(req.values or {})`（values 覆盖 CAD 映射结果）；
  4. `compose_profile(profile, incoming)` —— **内部已调 `validate_profile_values`**（`cad_material_sync_service.py:381`），**不要**再单独 `validate_profile_values`，避免重复校验/重复错误聚合；
  5. `_find_matching_items`（精确）→ 相似 service（模糊）。
- 输出：`composed_properties`、`exact_matches`、`similar_candidates`(含 `score`/`field_contributions`)、`draft_suggested`(bool)、`warnings`。
- **`draft_suggested` 定义**（统一口径，避免前端/测试各自解读）：**无 exact match 且无 `score >= 0.90` 高相似候选**时为 `true`；若存在高相似候选，则返回候选并要求用户确认（`draft_suggested=false`）。
- **硬约束**：不传 `create_if_missing`、不调用 `_apply_item_create`/`_apply_item_update`、不写 equivalence、不写任何业务表。

### 5.2 `POST .../assistant/create`（写，约束 2）
- 输入：用户确认后的 `profile_id` + `properties`（来自 resolve 的 composed）。
- 流程：`created_id = _apply_item_create(db, profile, properties, user)`（`cad_material_sync_service:507`，返回 `id`）→ **按 id 回读** `item = db.get(Item, created_id)` → 组装返回。
- 返回：`item_id`、`item_number = get_item_number(item.properties)`（`src/yuantus/meta_engine/services/item_number_keys.py:10`）、`state = item.state`、`current_state = item.current_state`（`Item.current_state` 列，存 lifecycle state id，见 `models/item.py:45`；**无** `current_state_id` 属性）、§5.3 的 `draft_check`。
- **硬约束**：不得用 `apply()` 的原始返回拼装（它只含 `{id,type,status}`，无 state/编码）。

### 5.3 Draft 口径校验（约束 6）
- 读该 ItemType 绑定 lifecycle 的 **start state**：`LifecycleState` 中 `is_start_state == True`（`lifecycle/models.py:43`；`attach_lifecycle` 取法见 `lifecycle/service.py:306-318`）。
- 校验：
  - **有 start state**：返回 `state` 必须等于 start state `name`、`current_state` 必须等于 start state `id`。
  - **无 lifecycle / 无 start state**：返回 `warning`，且不把它称为 Draft。
  - **有 start state 但不一致**（如 `state` 字符串为 `"New"` 而 start 为 `"Draft"`）：在 create 路径/attach 行为侧修正，**不**降级为"只看 `current_state`"。

## 6. 验收与测试

### 6.1 resolve 只读（约束 1）
- `resolve` 调用前后，对业务表（Item、关系、equivalence 等）做计数快照，断言 **delta == 0**。
- 覆盖 exact-only / similar-only / draft-suggested 三种返回形态。

### 6.2 create（约束 2/6）
- `create` 后返回含非空 `item_id`/`item_number`/`state`/`current_state`，且来自回读而非 `apply()` 返回。
- Draft 校验：有 start state 时 `state==start.name && current_state==start.id`；无 map 返 warning。该断言在当前 `add_op` 写死 `state="New"` 下**初次可能为红**——按 §5.3 修正路径，不放宽断言。
- 未确认不写：靠端点分离保证（resolve 永不写）。

### 6.3 相似评分（约束 4/5）
- key 生效：`material_category`/`finish` 修正后实际进入评分（对照 `field_contributions` 断言其权重非 0）。
- 量纲：同类不同尺寸（如 `Φ20*100` vs `Φ25*100`）必须跌出 `0.90` 高相似带。
- 归一自检：缺字段不拉低分；错写 key 触发告警。

### 6.4 CI 对账
- 新增测试（assistant + similarity）加入 `ci.yml` `plugin-tests` 显式清单（当前含 `test_plugin_cad_material_sync.py`，见 ci.yml `Plugin tests` step）。
- 新 env 若有，必在 `Settings` 声明且带 `YUANTUS_` 前缀。

## 7. 落点清单

- `src/yuantus/meta_engine/services/cad_material_similarity_service.py`（新，评分）
- `plugins/yuantus-cad-material-sync/main.py`（新增 2 个 assistant 路由 handler，复用 service）
- `src/yuantus/meta_engine/tests/test_plugin_cad_material_assistant.py`（新）
- `src/yuantus/meta_engine/tests/test_cad_material_similarity_service.py`（新）
- `.github/workflows/ci.yml`（plugin-tests 清单 + 上述新测试）

## 8. 排期顺序（每步以绿测试为门）

1. 相似评分 service + 单测（纯函数，最易隔离）。
2. `assistant/resolve`（只读）+ 零写入验收。
3. `assistant/create` + 回读 + Draft 校验（含 §5.3 路径修正）。
4. CI 清单接入 + 文档。

## 9. dedup_vision（约束 7，后置）

- 图纸相似（`DedupVisionClient.search_sync` → `DedupService.ingest_search_results`）继续放 feature flag，**不进 Phase 2 必达范围**。
- 理由：需 DWG/导出文件经上传链路到服务端，会拖住主线；字段相似已能闭环"相似推荐"。
- 留作 Phase 2 尾段可选 / 或独立 Phase 3 与 Helper Bridge 一起做。

## 10. 出口 / 交接

Phase 2 完成后，Phase 3（Helper Bridge `/material/assistant/*` 转发 + AutoCAD `PLMMATASSIST` + 可选 dedup_vision）可直接调这两条 assistant 路由。Phase 3 需单独开工授权。
