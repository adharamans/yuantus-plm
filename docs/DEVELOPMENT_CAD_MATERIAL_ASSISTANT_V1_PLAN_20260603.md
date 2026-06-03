# YuantusPLM CAD 物料助手 v1 开发计划（收紧版）

- Date: 2026-06-03
- Status: Plan (ready to build)
- Scope: 服务端共享物料同步 service + assistant `resolve`/`create` 编排 + 字段相似评分 + AutoCAD 入口
- 非目标：自动发布物料、自动改 BOM/替换、NL/LLM 必选能力、其他 CAD 客户端 UI 入口

## 1. 决策

v1 做「共享物料同步 service + assistant resolve/create + AutoCAD 入口」，不做单纯重包 API。

- v1 真实增量：**字段相似评分** 与 **统一助手编排**。
- v1 **不**迁移 `/compose`、`/validate` 到 Helper Bridge（assistant 在服务端内联调用共享 service，客户端只调 `/material/assistant/*`，旧命令保持直连）。
- v1 **不**强依赖 dedup_vision；图纸相似放 feature flag。
- v1 客户端入口仅 AutoCAD；SolidWorks/ZWCAD/GstarCAD 复用同一 bridge route，下一版接入。
- LLM/NL 只留 Provider 接缝，默认关闭（Settings 字段 `AI_PROVIDER=none`，运行时 env 为 `YUANTUS_AI_PROVIDER`；详见 §3.5）。

## 2. 现状与复用基线（grounding）

本计划在既有 `yuantus-cad-material-sync` 子系统之上做增量，不新建并行模块。已存在、必须复用的锚点：

- 插件路由：`plugins/yuantus-cad-material-sync/main.py`
  - `/compose`（:2234）、`/validate`（:2323）、`/diff/preview`（:2258）、`/sync/inbound`（:2388）、`/sync/outbound`（:2349）、`/profiles`（:2035）、`/config*`。
  - 多策略匹配 `DEFAULT_MATCH_STRATEGIES`（:266）→ 返回 `matched_items` / `ambiguous_match` / `conflict` / `not_found`。
  - 既有写路径 `_apply_item_create()`（:2013）→ `AMLEngine.apply(action=add)`（:2024）。
- 创建语义：`operations/add_op.py` 的 `execute()`，写死 `state="New"`（:74）后 `attach_lifecycle(...)`（:111）；`apply()` 仅返回 `{"id","type","status":"created"}`（:121-129）。
- 相似度（图纸）：`integrations/dedup_vision.py` 的 `DedupVisionClient.search_sync()`（:167，打 dedupcad-vision `/api/v2/search`）；结果消费 `meta_engine/dedup/service.py` 的 `ingest_search_results()`（:494，已带阈值/visual 评分）。
- 等效关系：`services/equivalent_service.py`（关系类型 `"Part Equivalent"`）—— 仅作"已识别物料"的补充等效，**不**作新件相似发现主源。
- 配置基类：`config/settings.py` 的 `class Settings(BaseSettings)`，`model_config` 含 `env_prefix="YUANTUS_"`（:11）+ `extra="ignore"`（:13）—— 字段对应的运行时 env 必须带 `YUANTUS_` 前缀，否则裸 env 被静默吞掉。
- 客户端迁移现状：material-sync 正迁入 CAD Helper Bridge（`docs/DEV_AND_VERIFICATION_CAD_HELPER_BRIDGE_S8_MATERIAL_SYNC_MIGRATION_R1_20260523.md`）；AutoCAD `MaterialSyncApiClient` 的 `DiffPreview/SyncInbound/SyncOutbound` 已经过 helper 转发，`/compose`、`/validate` 仍直连。

## 3. 后端实现

### 3.1 抽取 `yuantus-cad-material-sync` 共享 service

- 从插件 `main.py` 抽出 compose、validate、match、create 原语为可复用 service。
- 现有 `/compose`、`/validate`、`/sync/inbound`、`/sync/outbound` 继续存在、行为不变，只改为调用共享 service。
- assistant 不重写匹配/创建策略，只编排共享 service。
- 验收门：抽取后现有插件测试（含 `test_plugin_cad_material_sync.py` 等 14+ 合同测试）继续全绿。
- **CI 对账（抽取第一步必做）**：`ci.yml` 的 `plugin-tests` 显式清单当前只跑 `test_plugin_pack_and_go.py` 和 `test_plugin_bom_compare.py`（`.github/workflows/ci.yml:582-583`），**不含** `test_plugin_cad_material_sync.py`。服务抽取第一步必须把它（及新增 assistant 测试）加入该清单，否则"全绿"是假绿 —— 测试根本没在 CI 跑。

### 3.2 新增 assistant endpoint

- `POST /api/v1/plugins/cad-material-sync/assistant/resolve`
  - **严格只读**：内部执行 compose/validate/match/field-similar；不允许 `create_if_missing`，不写 Item、不写 equivalence、不写任何业务表。
  - 输入：`profile_id`、`cad_fields`、可选 `values`、可选 `file_id`/图纸上下文。
  - 输出：合成后 properties、精确匹配、字段相似候选、（flag 开启时）图纸相似候选、是否建议新增草稿。
- `POST /api/v1/plugins/cad-material-sync/assistant/create`
  - 仅在用户确认后调用，内部复用既有 create path（`_apply_item_create`）。
  - 返回值需**按 id 回查 Item 再组装**（`apply()` 返回值不含 state/编码）：`item_id`、`item_number`/物料编码、`state`、`current_state`。

### 3.3 字段相似评分 spec

#### 3.3.1 字段与 property key（对齐插件实际 key）

- 入站先归一别名：`category` / `material_profile` → **`material_category`**；其余按 profile `cad_mapping` 归一。
- 参评 key：`material_category`、`material`、`name`、`finish`、`heat_treatment`、`description`，以及 **profile 声明的量纲字段**（见 3.3.3）。
- 主表面处理字段是 **`finish`**（非 `finish_standard`；后者是 `required_when` 条件型 companion，不参与默认评分）。

#### 3.3.2 权重（合计 1.00，按"双方都有值"的字段重归一）

| 字段 | 权重 | 类型 | 比较方式 |
|---|---|---|---|
| `material_category` | 0.18 | 枚举 | 规范化精确相等 = 1，否则 0 |
| `material` | 0.22 | 牌号 | 规范化精确 = 1；前缀/token 命中给部分分（如 `Q235` vs `Q235B`） |
| 量纲（dimensions） | 0.30 | 数值 | 见 3.3.3，数值容差比较，取代对合成 `specification` 的 token 重叠 |
| `name` | 0.10 | 自由文本 | 规范化 token 重叠（Jaccard） |
| `finish` | 0.10 | 枚举 | 规范化精确 = 1，否则 0 |
| `heat_treatment` | 0.05 | 枚举 | 规范化精确 = 1，否则 0 |
| `description` | 0.05 | 自由文本 | 规范化 token 重叠（Jaccard） |

归一规则：仅当字段在 query 与 candidate **两侧都非空**时才计入分子与分母；任一侧为空则不计入分母（权重相对重归一）。

#### 3.3.3 量纲字段：数值感知，而非对 `specification` 做 token 重叠

- 量纲字段**从 compose `template` 引用中发现**（不是按"数值型字段"——真实 profile 里 `forging.blank_size` 的 `type` 是 `string`）。例：
  - `sheet`：`length`/`width`/`thickness`；`tube`：`outer_diameter`/`wall_thickness`/`length`；`bar`：`diameter`/`length`；`forging`：`blank_size`。
- 取值：`type=number` 的字段直接取数值比较；`blank_size` 这类尺寸串（`type=string`）先正则抽数值再比较。
- 单维得分：相对误差 `|a-b|/max(a,b) <= tol` 记 1.0，超出线性衰减到 0（`tol` 默认 0.02，profile/租户可配）；抽不到数值则该维不计入。
- 量纲总分 = 两侧均可解析维的得分均值。
- 回退：candidate 只有合成 `specification` 时，先正则抽数值做同样数值比较；都抽不到才退化为对 `specification` 的 token 重叠。
- 目的：修掉"`Φ20*100` 与 `Φ25*100` token 高度重叠却是不同规格"的误判。

#### 3.3.4 输出与阈值

- `score` 归一 `0..1`；`>= 0.75` 为相似候选，`>= 0.90` 为高相似/重复风险。
- 返回 top 10，排序 `score desc, updated_at desc, created_at desc`（Item 时间列为 `updated_at`/`created_at`，见 `models/item.py:33-36`；序列化 payload 字段名为 `modified_on`/`created_on`，:118-120 —— 不要用不存在的 `modified_at`）。
- 已有精确匹配优先展示，相似候选不与精确命中混排。
- 每候选附 `score` 与 `field_contributions`（各字段得分明细），用于前端解释与调权。

#### 3.3.5 自检兜底（防静默 no-op）

- service 启动/单测加断言：3.3.1 的 key 与 profile 量纲字段，必须能在至少一条 seed/fixture Item 上取到非空；任一 key 长期全空即告警，避免错写 key 时"看着能跑、实则丢权重"。

### 3.4 dedup_vision flag

- v1 默认关闭图纸相似。
- 开启后：CAD 文件上传到服务端（复用 Helper Bridge 上传原语）→ `DedupVisionClient.search_sync()` → `DedupService.ingest_search_results()`；不重写 visual score 与阈值。
- 无文件上下文时只返回字段相似。

### 3.5 配置

- 在 `src/yuantus/config/settings.py` 的 `Settings` 里**显式声明字段**（否则 `extra="ignore"` 静默吞掉）：
  - `AI_PROVIDER`（默认 `none`）、`AI_ALLOW_EXTERNAL`（默认 `false`）、后续 provider 所需字段。
- **字段名 ≠ 环境变量名**：因 `env_prefix="YUANTUS_"`（settings.py:11），上述字段对应的运行时环境变量为 **`YUANTUS_AI_PROVIDER` / `YUANTUS_AI_ALLOW_EXTERNAL`**。部署/文档/CI 里设置裸 `AI_PROVIDER` 会被静默忽略 —— 必须带 `YUANTUS_` 前缀。
- 默认 provider 为 `none`，规则版闭环不依赖外部模型。

## 4. Helper Bridge 与 AutoCAD

- Helper Bridge 新增并**仅**转发：
  - `POST /material/assistant/resolve` → `/plugins/cad-material-sync/assistant/resolve`
  - `POST /material/assistant/create` → `/plugins/cad-material-sync/assistant/create`
  - Helper 负责本地 token、PLM bearer、trace id、审计、错误归一。
- **不**新增 `/compose`、`/validate` 到 bridge（v1 不需要，旧命令保持直连）。
- AutoCAD 新增命令 `PLMMATASSIST`：读取当前 DWG 物料字段 → 调 Helper assistant route → 展示候选物料/相似物料/重复风险/新增草稿；用户确认后才调 create，并可回写 `item_id`/物料编码到 CAD 字段。
- SolidWorks/ZWCAD/GstarCAD v1 不加 UI/命令入口。

## 5. 验收与测试

### 5.1 后端
- 抽取后现有 material-sync 插件测试继续全绿。
- `resolve` 调用后**数据库业务表零写入**（断言）。
- `resolve` 返回能同时给出：exact match、ambiguous match、字段相似候选、（flag 开）dedup_vision 候选、draft suggestion。
- 相似评分：`material_category`/`finish` 等修正后的 key 实际生效（对照 `field_contributions` 断言权重未被静默吞）；量纲不同（如 `Φ20` vs `Φ25`）应跌出 `0.90` 高相似带。
- 阈值用例：构造 `score>=0.75` 候选与 `score>=0.90` 高相似各一，断言分带正确。
- `create` 未确认不得写入（靠端点分离保证：`resolve` 永不写）。
- `create` 后 item 的状态校验（**口径已定**：以 lifecycle 起始态为准，不改全局 `add_op`，也不只断言 `current_state`）。assistant/create 创建后必须回查 Item 并校验：
  - **有 lifecycle start state**：返回的 `state` 必须等于 start state name，`current_state` 必须等于 start state id。
  - **无 lifecycle map / 无 start state**：返回 warning，且**不**把它称为 Draft。
  - **有 start state 但 `state`/`current_state` 不一致**：实现阶段须修正 create 路径或 lifecycle attach 行为，**不得**降级为"只看 `current_state`"。
  - 背景：当前 `add_op` 写死字符串 `state="New"`（`operations/add_op.py:74`）后才 `attach_lifecycle`（:111），故本校验初次可能为红 —— 这是预期信号，按上述口径在 create 路径/attach 行为侧修正，而非放宽断言。
- provider 默认关闭可用（Settings 字段 `AI_PROVIDER` 默认 `none`；覆盖用 env `YUANTUS_AI_PROVIDER`，非裸 `AI_PROVIDER`）。

### 5.2 CAD/Helper
- Helper route 正确转发 assistant 请求并带认证/trace；contract 测试覆盖两条 assistant route。
- AutoCAD `MaterialSyncApiClient` 不直接调用 assistant 服务端路径（只经 Helper）。
- 新测试加入 `.github/workflows/ci.yml` 或 `cad-helper-shared-dotnet.yml` 的**显式清单**（仅新增测试文件不会被执行）。

### 5.3 手工验收
- 已有物料：CAD 字段命中并建议绑定。
- 相似物料：规格/材质接近时列出候选并说明差异（`field_contributions`）。
- 新物料：无高置信匹配时生成草稿，经确认创建 Draft Part。
- 外部模型关闭时完整流程仍可用。

## 6. 已知陷阱与对账项

- env 必须在 `Settings` 模型声明**且带 `YUANTUS_` 前缀**（`env_prefix="YUANTUS_"`），否则 `extra="ignore"` 静默丢弃 —— 运行时用 `YUANTUS_AI_PROVIDER` / `YUANTUS_AI_ALLOW_EXTERNAL`，不是裸 `AI_PROVIDER`。
- 新测试必须进 CI 清单（`ci.yml` / `cad-helper-shared-dotnet.yml`），否则静默不跑。
- 若改走 `app_framework` 扩展点：extension point 必须预先 seed，否则注册是静默 no-op（v1 默认走插件路由，不依赖此路径）。
- `add_op` 字符串 `state="New"`（:74）与 lifecycle 起始态不一致 —— 口径已在 §5.1 定死：以 lifecycle 起始态为准，create 路径侧修正，不放宽断言。
- 相似度 key 错配会被"空字段不计入分母"静默吸收 —— 由 3.3.5 自检兜底。

## 7. 排期

推荐顺序（每步以绿测试为门）：
1. 服务抽取并跑绿现有回归。
2. assistant `resolve`/`create`。
3. 字段相似评分。
4. Helper Bridge + AutoCAD `PLMMATASSIST`。
5. dedup_vision flag 评估是否进入 v1 尾段。

预计：
- 不含 dedup_vision：1.5–2 周。
- 含 dedup_vision 文件链路：2.5–3.5 周。

风险最高两段：服务抽取（2507 行插件中 governance/rollout/versioning 缠绕，抽净且不回归最磨人）、dedup_vision 图纸文件链路（DWG→helper 上传→服务端→dedup-vision）。
