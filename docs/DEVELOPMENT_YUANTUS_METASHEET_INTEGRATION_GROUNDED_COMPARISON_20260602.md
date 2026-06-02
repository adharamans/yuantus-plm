# YuantusPLM ↔ MetaSheet2 集成「双侧」落地 grounding（实证版）

Date: 2026-06-02
Scope: `YuantusPLM`（provider，`/Users/chouhua/Downloads/Github/Yuantus`）↔ `MetaSheet2`（consumer + 集成 hub，`/Users/chouhua/Downloads/Github/metasheet2`）之间**已落地**的集成面。
Method（双侧均**源码取证**，区分置信度）:
- **Yuantus 侧（provider）**：逐条核对 30 个契约 interaction 是否有真实路由承载，给 `file:line`；本人亲自 grep/Read，未经子代理。
- **MetaSheet 侧（consumer）**：`PLMAdapter.ts` 的 operation→endpoint 映射本人亲自 grep 取证（37 处 `/api/v1` 字面量）；`federation.ts` / `plugin-integration-core` / K3 适配器仅经子代理 excerpt 阅读 + 存在性/体量核对，**标 〔中〕**。
- **两份 pact 工件**：`md5` 比对 + `interactions` 集合差分（本人执行）。

置信度图例：〔高〕本人读了 `file:line`/函数体 ・〔中〕读了签名/路由/体量或经子代理 excerpt ・〔低/❓〕仅命名或未打开。
状态图例（仅指 **契约/路由/工件层是否对齐**，**不代表**「运行时行为闭环已审计」——后者见 §4/§7/§8）：✅已对齐 ・🟡部分/有缺口 ・❌缺失 ・❓待证实。

> ⚠️ 方法论纪律：本文头条经过一次**自我推翻**（见 §8）。第一轮 grep 因 router `prefix` 与多行装饰器产生「核心路由=0」假阴性；并曾误判 provider 验证「偏软」。两处均经第二轮实证更正——保留更正痕迹以示边界。

---

## 0. 头条洞察（最该记住的一句）

> **契约工件零漂移、provider 端有真验证门禁、consumer 端只有静态守卫——三者建立在同一份手写契约上，因此真正的薄弱腿是「契约 ↔ 消费者运行时实际所发」的保真度，而非 provider 的形状一致性。**

拆开说（每条都有 `file:line`，见后文）：

1. **工件层：零漂移**。两份 `metasheet2-yuantus-plm.json` **字节级完全一致**（`md5=3cf769fca4d4351bf0087dde282dd38d`，86078 bytes，各 30 个 interaction，集合完全相同）。由 `scripts/sync_metasheet2_pact.sh`（MetaSheet 为 source-of-truth）+ CI sync-helper 门禁强制。〔高〕
2. **路由层：30/30 全覆盖**。consumer 契约里的 30 个 endpoint，provider 侧逐条都有真实路由承载（本人 grep 到每个 `file:line`，见 §2）。〔高〕
3. **验证层：不对称**（这是本次最有价值的发现）：
   - **provider 端 = 真回放门禁，且本地实跑 green**：CI 装 `pact-python==3.2.1`，专设 "Pact provider verifier" 步骤起一个临时 SQLite+uvicorn 活实例、回放全部 30 个 interaction 并 `assert success`——**响应形状一致性是被真验证的**。**本人 2026-06-02 在 py3.11 + pact-python 3.2.1 本地等价复现通过：`1 passed, 18.62s`**。〔高〕
   - **consumer 端 = 静态守卫 + 与契约无绑定的单测**：契约测试未引入 `@pact-foundation/pact`（header 自述），只做「计数+顺序+路径字符串出现在 `PLMAdapter.ts` 源码 + pact 自身 body 形状」断言，**不对 mock provider 跑适配器**。另有 unit 测试（`plm-adapter-yuantus.test.ts`）确**断言真实请求形状**（如 `toHaveBeenCalledWith('/api/v1/search/', […])`、`'/api/v1/aml/query'`+`method:'POST'`、`'/api/v1/aml/metadata/Part'`），但断的是**手写 mock 期望，与 pact 工件无绑定**。〔高〕
4. **耦合后的结论**：「路由存在」≠「响应形状被验证」≠「消费者真的按契约发请求」。provider 验证器是**对着 pact 验证、不是对着真实消费者**；consumer 端的请求形状虽有 unit 测试，但**与 pact 无绑定**（断言对手写 mock 期望），且无 consumer 侧 pact 回放；而 pact 本身是**手工编写**（非从真实消费交互录制）。⇒ 若手写 pact 与 `PLMAdapter` 运行时实际所发（某个 query 参数/header/body 字段）有出入，**provider 验证器照样 green、consumer 两类测试也照样 green**（单测对的是它自己的 mock 期望、不是 pact），缺口只会在真实集成/E2E 暴露。这正是本仓一贯纪律「✅ 落地 ≠ 生产级闭环」在本集成上的具体形态。

---

## 1. 拓扑与关系定位

```
                consumer-driven pact (hand-authored, byte-identical 双份)
                ┌───────────────────────────────────────────────┐
   YuantusPLM   │  contracts/pacts/metasheet2-yuantus-plm.json    │   MetaSheet2
  (provider,    │  packages/core-backend/tests/contract/pacts/... │  (consumer + hub)
   FastAPI      └───────────────────────────────────────────────┘
   /api/v1/*) ◀───────── PLMAdapter.ts (yuantus v1 mode, 37×/api/v1) ──────┐
                                                                            │
                                            ┌── federation.ts (/api/federation/plm) 〔中〕
                                            ├── plm-workbench.ts (内部协作 UI) 〔中〕
                                            └── plugin-integration-core
                                                  plm:yuantus-wrapper (source) 〔中〕
                                                        │ → MetaSheet staging
                                                        ▼
                                                  erp:k3-wise-webapi (target, save-only 默认) 〔中〕 → Kingdee K3
```

- **Yuantus = provider**：元数据驱动的 PLM 服务（`meta_engine`），对外暴露 `/api/v1/*`。
- **MetaSheet = consumer + 集成 hub**：内核 `PLMAdapter.ts` 直连 Yuantus；`plugin-integration-core` 把它包成 `plm:yuantus-wrapper` source，经 MetaSheet staging 流向 `erp:k3-wise-webapi` target（Kingdee K3）。即 **PLM(Yuantus) → MetaSheet → ERP(K3)** 的搬运链，MetaSheet 是中枢。
- **契约方向**：consumer-driven —— MetaSheet 持有/编写 pact，Yuantus 同步并验证（§3）。

> **务必区分的两个「PLM→ERP」**（避免概念混淆）：
> - 本文主轴 = **MetaSheet 经 plugin 把 Yuantus 物料/BOM 投到 K3**（在 MetaSheet 仓）。
> - Yuantus 自带的 `src/yuantus/meta_engine/erp_publication/`（`adapter/http_adapter/service/worker/models/adapter_registry` + `plm_erp_publication_router.py` / `plm_erp_publication_outbox_router.py`）是**另一条** vendor-agnostic 出站投放脊柱（odooplm gap **G2 closeout**），**与 MetaSheet pact 轴无关**，勿混为一谈。〔高〕（文件存在性已核）

---

## 2. 契约面：30 个 interaction 双侧逐条核对（核心表）

> provider 列 = 本人 grep 到的真实路由 `file:line`（已穿透 `APIRouter(prefix=...)` 与多行装饰器）。consumer 列 = `packages/core-backend/src/data-adapters/PLMAdapter.ts` 内 callsite 行号（本人 grep）。状态 ✅ = 双侧均落地且 pact 集合一致。

| # | method + path | provider 路由（`src/yuantus/...`） | consumer callsite（`PLMAdapter.ts`） | 状态 |
|--:|---|---|---|:--:|
| 1 | POST `/api/v1/auth/login` | `api/routers/auth.py:34`（prefix `/auth` :16） | :881 | ✅ |
| 2 | GET `/api/v1/health` | `api/routers/health.py:25` | :942 | ✅ |
| 3 | GET `/api/v1/search/` | `meta_engine/web/search_router.py:20`（prefix `/search`） | :998 / :1179 | ✅ |
| 4 | POST `/api/v1/aml/apply` | `meta_engine/web/router.py:24`（meta_router prefix `/aml` :14） | :1562 / :1585 | ✅ |
| 5 | POST `/api/v1/aml/query` | `meta_engine/web/query_router.py:17`（prefix `/aml` :14） | :1689 | ✅ |
| 6 | GET `/api/v1/aml/metadata/{type}` | `meta_engine/web/router.py:60` | :1635 | ✅ |
| 7 | GET `/api/v1/bom/{id}/tree` | `meta_engine/web/bom_tree_router.py:199`（prefix `/bom` :15） | :1833 | ✅ |
| 8 | GET `/api/v1/bom/{id}/where-used` | `meta_engine/web/bom_where_used_router.py:70`（prefix `/bom` :14） | :2049 | ✅ |
| 9 | GET `/api/v1/bom/compare` | `meta_engine/web/bom_compare_router.py`（prefix `/bom` :18） | :2095 | ✅ |
| 10 | GET `/api/v1/bom/compare/schema` | `meta_engine/web/bom_compare_router.py:466` | :2147 | ✅ |
| 11 | GET `/api/v1/bom/{id}/substitutes` | `meta_engine/web/bom_substitutes_router.py:66`（prefix `/bom` :18） | :2165 | ✅ |
| 12 | POST `/api/v1/bom/{id}/substitutes` | `meta_engine/web/bom_substitutes_router.py` | :2193 | ✅ |
| 13 | DELETE `/api/v1/bom/{id}/substitutes/{sid}` | `meta_engine/web/bom_substitutes_router.py` | :2214 | ✅ |
| 14 | GET `/api/v1/eco` | `meta_engine/web/eco_core_router.py:119`（prefix `/eco` :23） | :1859 | ✅ |
| 15 | GET `/api/v1/eco/{id}` | `meta_engine/web/eco_core_router.py:155` | :1916 | ✅ |
| 16 | GET `/api/v1/eco/{id}/approvals` | `meta_engine/web/eco_approval_workflow_router.py:252`（prefix `/eco` :28） | :1970 | ✅ |
| 17 | POST `/api/v1/eco/{id}/approve` | `meta_engine/web/eco_approval_workflow_router.py:212` | :1996 | ✅ |
| 18 | POST `/api/v1/eco/{id}/reject` | `meta_engine/web/eco_approval_workflow_router.py:232` | :2017 | ✅ |
| 19 | GET `/api/v1/file/{id}` | `meta_engine/web/file_metadata_router.py:84`（prefix `/file` :23） | :1503 | ✅ |
| 20 | GET `/api/v1/file/item/{id}` | `meta_engine/web/file_attachment_router.py:162`（prefix `/file` :26） | :1688 | ✅ |
| 21 | GET `/api/v1/release-readiness/items/{id}` | `meta_engine/web/release_readiness_router.py:140` | :1494 / :1798 | ✅ |
| 22 | GET `/api/v1/cad/files/{id}/properties` | `meta_engine/web/cad_properties_router.py:32` | :2234 | ✅ |
| 23 | PATCH `/api/v1/cad/files/{id}/properties` | `meta_engine/web/cad_properties_router.py:54` | :2260 | ✅ |
| 24 | GET `/api/v1/cad/files/{id}/view-state` | `meta_engine/web/cad_view_state_router.py:108` | :2284 | ✅ |
| 25 | PATCH `/api/v1/cad/files/{id}/view-state` | `meta_engine/web/cad_view_state_router.py:133` | :2310 | ✅ |
| 26 | GET `/api/v1/cad/files/{id}/review` | `meta_engine/web/cad_review_router.py:31` | :2333 | ✅ |
| 27 | POST `/api/v1/cad/files/{id}/review` | `meta_engine/web/cad_review_router.py:51` | :2356 | ✅ |
| 28 | GET `/api/v1/cad/files/{id}/history` | `meta_engine/web/cad_history_router.py:31` | :2375 | ✅ |
| 29 | GET `/api/v1/cad/files/{id}/diff` | `meta_engine/web/cad_diff_router.py:38` | :2398 | ✅ |
| 30 | GET `/api/v1/cad/files/{id}/mesh-stats` | `meta_engine/web/cad_mesh_stats_router.py:65` | :2415 | ✅ |

**结论**：30/30 双侧落地，consumer 调用的端点 provider **全部**有真实路由承载，无「调了但 provider 没有」的悬空端点。〔高〕
**边界**：本表证明的是 **route 存在 + pact 集合一致**；**响应字段 schema 的一致性**由 §4 的 provider 验证器在 CI 中负责（落地，但条件触发）；各路由 handler 的**业务行为正确性未逐一审计**。

---

## 3. 契约同步机制（工件零漂移如何被强制）

| 事实 | 证据 | 置信 |
|---|---|---|
| 两份 pact 字节级一致 | 双份 `md5=3cf769fca4d4351bf0087dde282dd38d`，86078 bytes，各 30 interaction，`(method,path)` 集合完全相同（本人 Python 差分） | 〔高〕 |
| MetaSheet 是 source-of-truth | `scripts/sync_metasheet2_pact.sh`：`SOURCE=metasheet2/packages/core-backend/tests/contract/pacts/...`(:76)，`TARGET=contracts/pacts/...`(:77) | 〔高〕 |
| 漂移检测 + 同步 | `--check` 用 `cmp -s` 报 `pact_sync=ok|drift`(:91-95)；非 check 模式 `cp` 覆盖(:103) | 〔高〕 |
| CI 守护同步 | `test_ci_contracts_pact_sync_helper.py`（ci.yml:330 运行）；provider gate `test_ci_contracts_pact_provider_gate.py`（ci.yml:329） | 〔中〕（运行入口已见，断言体未深读） |
| 工件来源声明 | pact metadata 自述 hand-authored Wave 1-5（非录制） | 〔中〕 |

---

## 4. 验证严谨度：两侧不对称（本次核心发现）

### 4.1 Provider 侧 = 真 CI 回放门禁（落地，非软；但 change-scoped）

`src/yuantus/api/tests/test_pact_provider_yuantus_plm.py`：

- `:1186 test_yuantus_provider_verifies_local_pacts` 是唯一 `def test_`，但它是 **pact-verifier 入口**（遍历 `contracts/pacts/*.json` 全部 interaction），不是「只测一个」。
- `:1198 pytest.importorskip("pact")` —— **本地无 `pact-python` 即 skip**（故 `requirements.lock`/`pyproject.toml` 里查无 pact）。
- 运行时：`:1213 _isolated_test_database()`（临时 SQLite）→ `:1214 _running_provider()`（真 uvicorn 起活实例拿 `base_url`）→ `:1221 verifier.verify_pacts(*pact_files, provider_states_setup_url=...)` → `:1225 assert success, logs`。fixture 在 `:281 _seed_pact_fixtures`（`:1118` 调用）一次性预置。
- **CI 真的跑（且装 pact 与跑验证器同属一个 job——已核 job 边界）**：`.github/workflows/ci.yml` 的 `contracts` job 始于 `:200`（`name: contracts` :201，`needs: detect_changes_ci` :202，`if: ...run_contracts == 'true'` :203，`runs-on: ubuntu-latest` :204），下一个 job `plugin-tests` 在 `:540`。`:220 pip install ... pact-python==3.2.1` 与 `:536-538` 步骤 `Pact provider verifier (Metasheet2 -> Yuantus): pytest -q .../test_pact_provider_yuantus_plm.py` **同在 200–539 这个 `contracts` job 内**——故 CI 内 `importorskip("pact")` 不会触发，**不会静默 skip**。
- **触发条件**：`contracts` job 受 `detect_changes_ci` 的 change-scope 控制（`run_contracts` 由 `ci.yml:118` 的路径清单驱动，含 `contracts/pacts/*.json` 与被契约的 cad/eco/file routers）。⇒ 仅在**契约面文件有改动**的 PR 上运行。

**本地实证（2026-06-02，本人实跑）**：`python3.11 -m venv` + `pip install -r requirements.lock pytest pact-python==3.2.1` + `PYTHONPATH=src pytest -q src/yuantus/api/tests/test_pact_provider_yuantus_plm.py` → **`1 passed, 18.62s`**。日志可见对各 interaction `POST /_pact/state` 置态后回放、响应全部匹配。两条 warning（`cadquery`/`elasticsearch` 未装）为 benign 可选特性，search 走 DB fallback 仍验证通过。

> 因此准确表述为：**provider 形状一致性 = 已接入真 CI 门禁且已本地实证 green**（装 pact-python、同 job 回放 30 个 interaction 校验响应匹配、`assert success`，本地 `1 passed`），但 CI 内**按改动范围触发**，本地无 pact-python 时默认 skip。本人**未亲见 CI 实跑**，但本地等价复现已绿，故 wired + green 双重坐实。〔高〕（机制 + job 边界 + 本地实跑）

### 4.2 Consumer 侧 = 静态守卫（未接真 pact 运行时）

`metasheet2/packages/core-backend/tests/contract/plm-adapter-yuantus.pact.test.ts`：

- header `:5-28` **自述**：尚未引入 `@pact-foundation/pact`，**不从真实消费交互重建工件**（本人核 `package.json` 确无该依赖）。
- `:132-138`：断言 `interactions.length === PACT_PATHS.length` 且逐条 method/path 与**硬编码 `PACT_PATHS` 列表**按序匹配。
- `:151-189`：`adapterSrc.includes(ep)` —— 把 25+ 端点字符串逐个在 `PLMAdapter.ts` **源码文本**里找子串（防 pact 与 adapter 源码漂移），**不起 mock provider、不跑适配器**。
- `:192-215+`：对 `aml/apply` 等的 request/response body 形状断言——但断的是 **pact 文件自身声明的 body**，非真实 provider 响应。

**另有 unit 测试（与契约无绑定，但确实测了请求形状）**：`packages/core-backend/tests/unit/plm-adapter-yuantus.test.ts`（1148L）用 mock 的 `query`/`select` 传输层断言 `PLMAdapter` 真实发出的 path/method/params：`:73`、`:130` `toHaveBeenCalledWith('/api/v1/search/', […])`；`:204` `'/api/v1/file/item/item-1'`；`:205-206` `'/api/v1/aml/query'` + `method:'POST'`；`:213` `'/api/v1/file/file-1'`；`:306` `'/api/v1/aml/metadata/Part'` 等。**但**这些是**手写 mock 期望**，**不从 pact 派生、不与 pact 工件交叉校验**。

> 因此 consumer 端的请求形状**有 unit 测试覆盖**（非「完全无验证」），但它**未与 pact 绑定**：单测对的是它自己的期望、不是契约工件；也**没有**对 mock provider 的真消费者 pact 回放（无 `@pact-foundation/pact`，header 自述「待落地」）。即「consumer 真按契约发、provider 真按契约回」这一**双向命题**仍未被任一侧的契约机制锁定。〔高〕

### 4.3 耦合判断

| 被验证的命题 | 谁来验 | 落地? |
|---|---|:--:|
| 工件两份一致 | sync 脚本 + CI sync gate | ✅ |
| consumer 调用的端点 provider 都有路由 | 本 grounding §2 + provider 验证器隐含 | ✅ |
| provider 响应**字段形状**符合契约 | provider verifier（CI，change-scoped） | ✅（条件触发） |
| consumer **请求形状**（path/method/params） | unit 测试 `plm-adapter-yuantus.test.ts`（对手写 mock 期望） | ✅（但与 pact 无绑定） |
| consumer 请求形状**与契约绑定** / 真消费者 pact 回放 | —（无 `@pact-foundation/pact`） | 🟡 **薄弱腿** |
| 端到端 PLM→MetaSheet→K3 真链路 | mock E2E 有；live 受客户 GATE 阻塞 | 🟡（见 §7） |

---

## 5. MetaSheet 侧消费拓扑（置信度显式分层）

| 组件 | 文件（体量） | 角色 | 置信 |
|---|---|---|:--:|
| `PLMAdapter.ts` | `packages/core-backend/src/data-adapters/PLMAdapter.ts`（2513L，37×`/api/v1`） | yuantus v1 直连客户端，承载 30 契约端点 | **〔高〕本人 grep operation→endpoint 全表** |
| `plm-adapter-yuantus.pact.test.ts` | `.../tests/contract/`（530L） | 静态契约守卫（§4.2） | **〔高〕本人 Read** |
| `plm-adapter-yuantus.test.ts` | `.../tests/unit/`（1148L） | 请求形状单测（对手写 mock，**不绑 pact**） | **〔高〕本人 grep 断言行** |
| `federation.ts` | `packages/core-backend/src/routes/federation.ts`（3267L） | `/api/federation/plm` 统一查询/审批分发 | 〔中〕子代理 excerpt + 体量 |
| `plm-workbench.ts` | `packages/core-backend/src/routes/plm-workbench.ts`（3094L） | PLM 团队协作 UI 后端（filter preset / team view / audit） | 〔中〕子代理 + 体量 |
| `plugin-integration-core` | `plugins/plugin-integration-core/index.cjs`（290L）+ `lib/adapters/plm-yuantus-wrapper.cjs`（462L） | 把 PLMAdapter 包成 `plm:yuantus-wrapper` source；`/api/integration/*` 控制面 | 〔中〕子代理 + 体量 |
| `k3-wise-webapi-adapter.cjs` | `plugins/.../lib/adapters/`（1025L） | K3 target；`autoSubmit/autoAudit` 经 `resolveAutoFlag` 决策（:819/:887/:919），默认 save-only | 〔中〕本人见 gating 分支，未读 `resolveAutoFlag` 默认值 |

> 注：`metasheet2-plm-workbench` 兄弟目录**非 git worktree**（无 `.git`），真实 plm-workbench 代码在主 checkout 的 `packages/core-backend/src/routes/plm-workbench.ts`，勿追兄弟目录。

---

## 6. 真实差距 / 风险（按「影响 × 暴露面」排序）

| # | 差距/风险 | 影响 | 依据 | 置信 |
|---|---|:--:|---|:--:|
| R1 | **consumer 请求形状未与契约绑定**（§4.2/§4.3 薄弱腿）：请求形状有 unit 测试，但断的是手写 mock 期望、不从 pact 派生；且无 consumer 侧 pact 回放（不接 `@pact-foundation/pact`）。⇒ pact ↔ `PLMAdapter` 真发请求若漂移，provider 验证器（对 pact）与 consumer 单测（对自身 mock）**双双不报**。 | 高 | `package.json` 无该依赖；unit 测试断言对手写 mock（`plm-adapter-yuantus.test.ts`） | 〔高〕 |
| R2 | **provider 门禁 change-scoped**：仅契约面改动的 PR 跑验证器；非契约改动可能引入响应漂移而当次 PR 不触发该 job。 | 中 | `ci.yml:118` change-scope 清单 | 〔中〕 |
| R3 | **K3 写回仍 PoC/save-only**：`autoSubmit/autoAudit` 默认关闭，live 客户连通受 GATE 阻塞（参见 MetaSheet 仓 integration-k3wise 系列 evidence/preflight 脚本）。 | 中 | 子代理报告 + gating 分支 | 〔中〕 |
| R4 | **provider 路由 handler 行为未逐一审计**：§2 证明 route+契约形状，未证每个 handler 的业务正确性（如 eco 审批副作用、bom compare 三模式语义）。 | 中 | 本 grounding 范围所限 | 〔高〕（边界声明） |

---

## 7. 建议（落地、低风险、不越权）

> 均为「补验证/补取证」类，**不改契约语义**；任何代码改动需另行 per-phase opt-in。

1. **【R1·最高】闭合 consumer 验证腿**：在 MetaSheet 引入 `@pact-foundation/pact`，把现有静态守卫升级为对 mock provider 的真消费者 pact 测试，使「consumer 运行时所发」也被工件锁定。这是把本集成从「✅ 工件对齐」推向「✅ 双向回放闭环」的唯一关键投资。
2. **【R2·中】门禁去 change-scope 盲区**：让 provider verifier 在「任何可能影响 `/api/v1/*` 响应形状」的 PR 上也跑（或 nightly 全量），堵住非契约改动引入响应漂移的窗口。
3. **【R4·中】对 30 端点补 handler 级契约测试**：在 provider 形状验证之外，补关键端点（eco approve/reject、bom compare、release-readiness）的行为断言。
4. ~~**【取证·低】跑一次 CI 或本地装 `pact-python==3.2.1` 实跑 provider verifier**~~ → **已完成（2026-06-02）**：本地 py3.11 实跑 `1 passed, 18.62s`，「门禁已 wired」已升级为「本地实证 green」（见 §4.1）。

---

## 8. 方法论诚实：撤回与未核实声明

**本轮自我推翻（保留痕迹）**：
| 第一轮判断 | 更正 | 原因 |
|---|---|---|
| 「核心路由 search/aml/bom-compare = 0」 | ❌ 错，实为 **30/30 全有**（§2） | grep 未穿透 `APIRouter(prefix="/aml")` + 多行装饰器，连续字符串 `aml/apply` 从不出现 → 假阴性 |
| 「provider 验证偏软/可能静默跳过」 | ⚠️ 一半错：**本地** skip 属实，但 **CI 装 `pact-python==3.2.1` 真跑回放门禁**；且 install(:220) 与验证器(:536) **同属 `contracts` job**（:200-539），CI 内不 skip | 只读了 `importorskip` 未查 `ci.yml:220/536` 的 CI 依赖、步骤与 job 边界 |
| 「consumer 端只有静态字符串守卫」 | ⚠️ 收窄：另有 unit 测试**确实断言真实请求形状**，故薄弱腿应精确表述为「请求形状有单测、但**与 pact 无绑定**」 | 初稿只开了 consumer **pact** 测试，未开 **unit** 测试 `plm-adapter-yuantus.test.ts` |

**未核实 / 低置信边界**：
- provider verifier 已**本地实证 green**（2026-06-02，`1 passed, 18.62s`）；但**未亲见 CI 内实跑**（CI 为 change-scoped），故 CI green 属机制推断 + 本地等价复现，非直接观测 CI。
- MetaSheet 侧 `federation.ts` / `plm-workbench.ts` / `plugin-integration-core` / K3 适配器的**运行时行为**仅经子代理 excerpt + 体量，标 〔中〕，未本人深读。
- `k3-wise-webapi-adapter.cjs` 的 `resolveAutoFlag` **默认值**未读（save-only 默认属「声称」级）。
- pact `test_ci_contracts_pact_*_gate.py` 的**断言体**未深读（只确认 CI 运行入口）。
- §2 各 provider handler 的**业务行为正确性**不在本文取证范围。

---

## 9. 附录：可复现取证命令

```bash
# A. 两份 pact 是否字节一致 + interaction 集合差分
md5 Yuantus/contracts/pacts/metasheet2-yuantus-plm.json \
    metasheet2/packages/core-backend/tests/contract/pacts/metasheet2-yuantus-plm.json
python3 -c 'import json;a=json.load(open("Yuantus/contracts/pacts/metasheet2-yuantus-plm.json"));print(len(a["interactions"]),a["consumer"],a["provider"])'

# B. provider 路由穿透 prefix/多行装饰器后逐条核对（示例：aml/bom-compare）
grep -rInE 'APIRouter\(prefix="/aml"|\.(post)\("/(apply|query)"' Yuantus/src/yuantus/meta_engine/web
grep -rInE 'APIRouter\(prefix="/bom"|"/compare/schema"' Yuantus/src/yuantus/meta_engine/web

# C. consumer operation→endpoint 自验
grep -nE "/api/v1/" metasheet2/packages/core-backend/src/data-adapters/PLMAdapter.ts

# D. 验证严谨度两侧
grep -nE "importorskip|verify_pacts|assert success" Yuantus/src/yuantus/api/tests/test_pact_provider_yuantus_plm.py
grep -nE "pact-python|Pact provider verifier" Yuantus/.github/workflows/ci.yml
# D2. install(:220) 与验证器(:536) 是否同一 job —— 取各自上方最近的 2 空格 job header
awk 'NR<=220 && $0 ~ /^  [A-Za-z_][A-Za-z0-9_-]*:[[:space:]]*$/ {h=$0} END{print "owns 220:",h}' Yuantus/.github/workflows/ci.yml
awk 'NR<=536 && $0 ~ /^  [A-Za-z_][A-Za-z0-9_-]*:[[:space:]]*$/ {h=$0} END{print "owns 536:",h}' Yuantus/.github/workflows/ci.yml   # → 两者皆 "contracts:" = 同 job
# D3. consumer 两类测试
grep -nE "@pact-foundation/pact" metasheet2/packages/core-backend/package.json   # → 空 = 无真消费者 pact 回放
grep -nE "toHaveBeenCalledWith\('/api/v1" metasheet2/packages/core-backend/tests/unit/plm-adapter-yuantus.test.ts  # → 请求形状单测（对手写 mock）

# E. provider verifier 本地实证 green（2026-06-02，本人实跑，与 CI 等价）
python3.11 -m venv /tmp/yuantus_pact_venv
/tmp/yuantus_pact_venv/bin/python -m pip install -q -r requirements.lock pytest pact-python==3.2.1
PYTHONPATH=src /tmp/yuantus_pact_venv/bin/python -m pytest -q \
  src/yuantus/api/tests/test_pact_provider_yuantus_plm.py
# → 1 passed, 18.62s（回放全部 30 个 interaction；ES/cadquery 未装的 warning 为 benign）
```

**关键证据文件**：
- 契约：`contracts/pacts/metasheet2-yuantus-plm.json`（Yuantus）/ `packages/core-backend/tests/contract/pacts/...`（MetaSheet）
- 同步：`Yuantus/scripts/sync_metasheet2_pact.sh`
- provider 验证：`Yuantus/src/yuantus/api/tests/test_pact_provider_yuantus_plm.py` + `Yuantus/.github/workflows/ci.yml:220,536-538`
- consumer 验证：`metasheet2/packages/core-backend/tests/contract/plm-adapter-yuantus.pact.test.ts`
- consumer 客户端：`metasheet2/packages/core-backend/src/data-adapters/PLMAdapter.ts`
- §2 provider 路由：`src/yuantus/meta_engine/web/{search,query,router,bom_*,eco_*,file_*,release_readiness,cad_*}_router.py` + `src/yuantus/api/routers/{auth,health}.py`
