# Phase 3.4 Readiness Taskbook — 2026-05-19

**Status**: Doc-only / read-only inventory. **Not a plan, not a PR, not authorization.**
**Trigger**: 用户口令 `Go P3.4 readiness taskbook`（精确匹配，per `memory/feedback_p3_4_readiness_taskbook_trigger.md`）
**Branch**: `design/p3-4-readiness-taskbook-20260519`（off `origin/main` = `dd88ea2`）
**Authoritative anchor**: `main=dd88ea2`（与 #509 refresh 后的 `main=89ba973` 在 P3.4 状态上一致；后续提交均为 Tier-B / Phase 4 / Phase 6 工作，未触及 P3.4 readiness）

本 taskbook 仅做 5 项盘点（per 触发口令契约）。**无**实现建议；**未**碰 schema / runtime / operator-pack；**未**新增任何 P3.4 实现资源。

---

## 0. 当前结论一句话

P3.4 本地工具链 + 本地安全加固 + post-P6 外部 evidence 文档 handoff 全部 **本地就绪**；唯一 outstanding blocker 是 **外部 operator 运行真实非生产 PostgreSQL rehearsal 证据 + reviewer 接受**。`ready_for_cutover=false` 是不可破契约。这条结论自 #508 / #509（2026-05-11）以来未变。

---

## 1. Phase 3 拓扑速览（用于定位本 taskbook 范围）

| 子相位 | 内容 | 当前状态 | 关键 PR / 提交 |
|---|---|---|---|
| **P3.1** | 设计/决策（schema-per-tenant 路线选定） | ✅ 已落地 | — |
| **P3.2** | 运行时 resolver + 调度（`tenant_id_to_schema()`、`get_db()` schema dispatch、default-off） | ✅ 已合并至 `main=80cc9dc` | `src/yuantus/database.py` |
| **P3.3.1** | tenant Alembic env（`migrations_tenant/`）、`alembic_tenant.ini`、settings | ✅ 已合并 | `migrations_tenant/env.py`、`alembic_tenant.ini` |
| **P3.3.2** | provisioning helper + 运维 runbook | ✅ 已合并 | `src/yuantus/scripts/tenant_schema.py`、`docs/RUNBOOK_TENANT_MIGRATIONS_20260427.md` |
| **P3.3.3** | 初始 tenant baseline revision | ⏳ **延后**（可与 P3.4 cutover 同期落、由 operator 在 stop-gate review 决定） |
| **P3.4** | tenant 数据 import / cutover rehearsal | 🔴 **本 taskbook 范围**（详见 §2–§5） |
| **Phase 5** | 生产 cutover | ⛔ 阻塞 —— 等待 P3.4 外部证据接受 |

**关键约束（自 P3.1 §5 起延续，contract test 强制）**：
- `migrations/env.py`（主 env）零改动；`migrations_identity/env.py` 零改动；二者均不包含 tenant 应用表
- `migrations_tenant/versions/` 在 P3.3 范围**故意为空**（empty by design），P3.3.3 baseline revision 单独评审
- `TENANCY_MODE=schema-per-tenant` 在任何环境**未启用**
- `DROP SCHEMA` 在 P3.3 / P3.4 范围**不允许**

---

## 2. 盘点支柱 #1 — Tenant Import 现有证据

### 2.1 实现资源（已合并）

| 类型 | 路径 | 用途 |
|---|---|---|
| 模块 | `src/yuantus/scripts/tenant_import_rehearsal.py` | 主 rehearsal 入口（`--confirm-rehearsal` 守门） |
| 模块 | `src/yuantus/scripts/tenant_import_rehearsal_plan.py` | import plan 清单生成（`ready_for_importer=true` 门控） |
| 模块 | `src/yuantus/scripts/tenant_import_rehearsal_source_preflight.py` | 源 DB preflight（`ready_for_importer_source=true`） |
| 模块 | `src/yuantus/scripts/tenant_import_rehearsal_target_preflight.py` | 目标 schema preflight（`ready_for_importer_target=true`） |
| 模块 | `src/yuantus/scripts/tenant_import_rehearsal_implementation_packet.py` | Claude implementation packet（`ready_for_claude_importer=true`） |
| 模块 | `src/yuantus/scripts/tenant_import_rehearsal_next_action.py` | next-action 报告（`claude_required=true`） |
| 模块 | `src/yuantus/scripts/tenant_import_rehearsal_synthetic_drill.py` | DB-free 命令路径 synthetic drill（`real_rehearsal_evidence=false` 永远） |
| 模块 | `src/yuantus/scripts/tenant_import_rehearsal_evidence.py` | operator evidence 模板生成 |
| 模块 | `src/yuantus/scripts/tenant_import_rehearsal_evidence_archive.py` | archive manifest（artifact hash） |
| 模块 | `src/yuantus/scripts/tenant_import_rehearsal_evidence_handoff.py` | evidence handoff gate |
| 模块 | `src/yuantus/scripts/tenant_import_rehearsal_redaction_guard.py` | artifact redaction guard |
| 模块 | `src/yuantus/scripts/tenant_import_rehearsal_external_status.py` | 外部 operator 进度 status checker |
| 模块 | `src/yuantus/scripts/tenant_import_rehearsal_operator_request.py` | operator 请求 packet |
| 模块 | `src/yuantus/scripts/tenant_import_rehearsal_operator_packet.py` | operator 执行 packet 生成 |
| 模块 | `src/yuantus/scripts/tenant_import_rehearsal_evidence_intake.py` | reviewer intake checklist |
| 模块 | `src/yuantus/scripts/tenant_import_rehearsal_reviewer_packet.py` | reviewer 决策 packet |
| Shell | `scripts/generate_tenant_import_rehearsal_env_template.sh` | repo-external env-file 模板生成 |
| Shell | `scripts/precheck_tenant_import_rehearsal_env_file.sh` | DB-free env-file 静态 precheck |
| Shell | `scripts/run_tenant_import_rehearsal_full_closeout.sh` | full-closeout 包装器（`--confirm-rehearsal` + `--confirm-closeout`） |
| Shell | （多份运维包装脚本）`scripts/run_tenant_import_rehearsal_*.sh` | command pack / sequence / launchpack / precheck / closeout 系列 |

### 2.2 安全加固层（已合并，contract 全绿）

按时间顺序回溯（依据 `PHASE3_TENANT_IMPORT_READINESS_STATUS_20260430.md` 与 git log）：

| 加固项 | PR / Commit |
|---|---|
| repo-external env-file 模板生成 | (≤ #465) |
| DB-free env-file 静态 precheck | (≤ #468) |
| env-file 支持 operator command pack / full-closeout 包装器 | |
| generated operator command-file validator | |
| command-file + env-file source safety 硬化 | |
| wrapper-level unsafe env-file source guard | #469 |
| runbook operator safety contracts | #470 |
| 源/目标 URL env-name allowlist 硬化 | #472 |
| env-file key allowlist before shell source | #473 |
| generated command-file 可执行行 allowlist | #474 |
| generated command-file 选项行 allowlist | #475 |
| generated command-file safe path option validation | #476 |
| generated command-file quoted metadata expansion guard | #477 |
| generated command-file shell syntax diagnostic redaction | #478 |
| validator CLI error redaction | #479 |
| env-file precheck CLI error redaction | #480 |
| shell wrapper CLI error redaction | #481 |
| Python module CLI error redaction | #482 |
| parent TODO safety status reconciliation | #471 / #483 |

### 2.3 外部 evidence handoff（post-P6 doc 层，2026-05-11）

| 文档 | PR | 内容 |
|---|---|---|
| `docs/PHASE3_TENANT_IMPORT_EXTERNAL_EVIDENCE_HANDOFF_PACKET_20260511.md` | #506 | 外部 operator 最短认可路径；列必填 inputs + acceptance boundary + explicit rejections |
| `docs/PHASE3_TENANT_IMPORT_EXTERNAL_EVIDENCE_REVIEW_CHECKLIST_20260511.md` | #507 | reviewer 接受 / 拒收清单 |
| `docs/DEV_AND_VERIFICATION_PHASE3_TENANT_IMPORT_EXTERNAL_EVIDENCE_*` | #506-#508 | 对应验证 MD |
| `docs/PHASE3_TENANT_IMPORT_READINESS_STATUS_20260430.md` | 多次更新 | 当前 readiness 主台账（2026-05-11 update 在内） |

> **关键事实**：handoff packet + reviewer checklist 只关闭"本地 handoff/文档" gap，**不**生成 evidence、**不**标记 P3.4 完成、**不**解锁 Phase 5。

### 2.4 缺失证据（唯一 outstanding 项）

**Outstanding，blocker，gating Phase 5**：

```
- [ ] Add operator-run PostgreSQL rehearsal evidence.
```

来源（必须同时出现且 contract 强制）：
- `docs/PHASE3_TENANT_IMPORT_REHEARSAL_TODO_20260427.md` 第 84 行
- `docs/PHASE3_TENANT_IMPORT_READINESS_STATUS_20260430.md` 末尾 §2 + §4 + §7
- `docs/PHASE3_TENANT_IMPORT_READINESS_STATUS_TODO_20260430.md` 末尾
- 由 `src/yuantus/tests/test_tenant_import_rehearsal_stop_gate_contracts.py` 守护（5 个 surface 中第 1 个）

---

## 3. 盘点支柱 #2 — Provisioning 现有证据

### 3.1 实现资源（已合并）

| 类型 | 路径 | 用途 |
|---|---|---|
| Helper 模块 | `src/yuantus/scripts/tenant_schema.py` | `provision_tenant_schema(tenant_id, *, create=True)` → 解析 + `CREATE SCHEMA IF NOT EXISTS`；Postgres-only；幂等；无 GRANT / 无 DROP |
| CLI 子命令 | `python -m yuantus.scripts.tenant_schema {resolve\|create} --tenant-id=<id>` | `resolve` = dry-run；`create` = 实际 DDL（仅在配置 DSN 指向非生产 Postgres 时） |
| Settings | `src/yuantus/config/settings.py` 中 `YUANTUS_ALEMBIC_TARGET_SCHEMA` / `YUANTUS_ALEMBIC_CREATE_SCHEMA` | 两者均 **默认 off**；仅由 tenant Alembic env + provisioning helper 读 |
| Tenant Alembic env | `migrations_tenant/env.py` | Postgres-only 守门、`^yt_t_[a-z0-9_]+$` schema 名校验、`SET search_path` + `version_table_schema=<target_schema>`、`include_schemas=True` |
| Config | `alembic_tenant.ini` | 与 `migrations_identity/` 对称 |
| 测试 | `src/yuantus/tests/test_tenant_alembic_env.py` | `GLOBAL_TABLE_NAMES`（12 项 = 7 identity + 4 RBAC + 1 legacy users）穷尽划分契约；offline `--sql` 第一行 `SET search_path` 契约；`version_table_schema` 契约 |
| 测试 | `src/yuantus/tests/test_tenant_schema_provision.py` | provisioner 行为契约；非 Postgres URL 抛错；幂等；无 DROP / 无 GRANT 静态检查 |

### 3.2 三组控制平面表（**禁止**入 tenant schema）

由 `migrations_tenant/env.py` `GLOBAL_TABLE_NAMES` frozenset 强制：

| 组 | 表数 | 表名 |
|---|---|---|
| Identity | 7 | `auth_tenants` / `auth_organizations` / `auth_users` / `auth_credentials` / `auth_org_memberships` / `auth_tenant_quotas` / `audit_logs` |
| RBAC | 4 | `rbac_resources` / `rbac_permissions` / `rbac_roles` / `rbac_users` |
| Legacy | 1 | `users` |

合计 12 项。**穷尽划分 contract** 要求 `combined == GLOBAL_TABLE_NAMES | tenant_set` 且二者 disjoint —— 防新增 global 表漏报 / 新增 tenant 表被误归类。

### 3.3 运维 runbook 锚点

`docs/RUNBOOK_TENANT_MIGRATIONS_20260427.md` —— 7 步生产安全序列：
1. 确认 tenant id 在 operator 批准列表
2. `python -m yuantus.scripts.tenant_schema resolve --tenant-id=<id>` 核对 schema 名
3. `python -m yuantus.scripts.tenant_schema create --tenant-id=<id>`
4. `alembic -c alembic_tenant.ini -x target_schema=<schema> upgrade head --sql > tenant_<schema>.sql`
5. operator 强制审阅 `--sql` 输出（**第一行**必须是 `SET search_path TO "<target>", public;`，**之前**无 DDL）
6. `alembic upgrade head`（P3.3.3 baseline revision 落地前为 no-op wiring 检查）
7. wiring smoke（schema 存在于 `pg_namespace`）；**不**断言 "应用表存在"

### 3.4 Provisioning 端缺失

- **无**。P3.3 provisioning 部分**已完整落地** —— 这是 P3.4 的前置依赖且已满足。
- 唯一相关 outstanding：P3.3.3 tenant baseline revision 是否在 P3.4 之前落，由 operator 在 stop-gate review 决定（见 §6 Open Questions）。

---

## 4. 盘点支柱 #3 — Rehearsal 现有证据

> Rehearsal = "P3.4 import 之前的演练"。Provisioning（§3）只创建 schema、不动数据；Rehearsal 是把数据从 db-per-tenant 拷到 schema-per-tenant、产证据。

### 4.1 三层 rehearsal 保护

| 层 | 工件 | 状态 |
|---|---|---|
| **本地 toolchain**（在仓库内可跑） | `tenant_import_rehearsal_*.py` 系列 + `scripts/run_tenant_import_rehearsal_*.sh` 系列 | ✅ 完整 |
| **本地 toolchain 安全加固**（防 operator 误操作） | 19 项加固（见 §2.2 表） | ✅ 完整 |
| **外部 operator 真实非生产 PG rehearsal** | 真实 DSN、真实 row copy、真实证据 | 🔴 **缺失** |

### 4.2 本地 toolchain 与 synthetic drill 边界

**Synthetic drill 不是 evidence**。`tenant_import_rehearsal_synthetic_drill.py` 永远满足：

```python
report["synthetic_drill"] is True
report["real_rehearsal_evidence"] is False
report["db_connection_attempted"] is False
report["ready_for_operator_evidence"] is False
report["ready_for_evidence_handoff"] is False
report["ready_for_cutover"] is False
```

由 `test_tenant_import_rehearsal_stop_gate_contracts.py` 的 `test_synthetic_drill_runtime_contract_keeps_real_gates_closed` 强制。

且 synthetic drill 源码**不**导入 `tenant_import_rehearsal_evidence_archive` / `tenant_import_rehearsal_evidence_handoff` —— 由静态源码扫描契约强制（`test_synthetic_drill_source_does_not_call_real_archive_or_handoff_gates`）。

### 4.3 Acceptance Boundary（真实 evidence 接受门槛）

外部 evidence chain 接受需**同时**满足（per `PHASE3_TENANT_IMPORT_EXTERNAL_EVIDENCE_HANDOFF_PACKET_20260511.md` §5）：

```
Import executed:               true
DB connection attempted:       true
Rehearsal evidence accepted:   true
Operator evidence accepted:    true
Ready for evidence intake:     true
Ready for evidence handoff:    true
Ready for reviewer packet:     true
Ready for cutover:             FALSE   ← 不可破契约
```

最后一行的 `false` 是 P3.4 完成后 **Phase 5 依然不被自动解锁**的关键保护 —— 任何 evidence artifact 标 `Ready for cutover: true` 都会被 reviewer 拒收。

### 4.4 Explicit Rejections（来自 handoff packet §6）

- synthetic drill 输出
- 仅本地命令路径 rehearsal
- mock 源 / 目标 DSN
- 未连接真实非生产 PostgreSQL 的 reviewer packet
- 任何含 PG 明文密码的 artifact
- 任何标 `Ready for cutover: true` 的 artifact

### 4.5 Rehearsal 端缺失

**唯一 outstanding 项**与 §2.4 同：
```
- [ ] Add operator-run PostgreSQL rehearsal evidence.
```

需要的具体输入（per `PHASE3_TENANT_IMPORT_EXTERNAL_EVIDENCE_HANDOFF_PACKET_20260511.md` §3）：
- 命名 pilot tenant
- 非生产 PG **源** DSN（仓库外）
- 非生产 PG **目标** DSN（仓库外）
- backup/restore owner（命名）
- rehearsal window（已排）
- 已签字的 table classification artifact
- evidence reviewer（命名）

---

## 5. 盘点支柱 #4 — Stop-Gate 状态（5 个 surface）

### 5.1 Stop-gate contracts 概览

权威契约文件：`src/yuantus/tests/test_tenant_import_rehearsal_stop_gate_contracts.py`

设计文档（`DEV_AND_VERIFICATION_PHASE3_TENANT_IMPORT_STOP_GATE_CONTRACTS_20260430.md` §3）声明 **5 个 surface**；契约文件中拆为 8 个测试函数（细化）。下面按 5-surface 框架列出。

### 5.2 五个 stop-gate surface

#### Surface 1 — parent P3.4 TODO

- **文件**：`docs/PHASE3_TENANT_IMPORT_REHEARSAL_TODO_20260427.md`
- **契约**：`test_parent_todo_keeps_real_operator_evidence_unchecked_after_synthetic_drill`
- **状态**：✅ 契约绿
  - 19 项本地加固 `- [x]` 已 check
  - `- [ ] Add operator-run PostgreSQL rehearsal evidence.` **必须保持未 check**；`- [x]` 形式被显式断言**不**得出现
- **Stop 语义**：仓库内任何 PR 都不得偷偷把 outstanding 标完成

#### Surface 2 — tenant migration runbook

- **文件**：`docs/RUNBOOK_TENANT_MIGRATIONS_20260427.md`
- **契约**：4 个相关测试
  - `test_runbook_warns_synthetic_drill_is_not_operator_run_evidence` —— 显式警告 "This output is not operator-run PostgreSQL rehearsal evidence." + "do not mark the P3.4 stop gate complete from synthetic output"
  - `test_runbook_pins_env_file_precheck_before_wrapper_source` —— 强制顺序：precheck → source
  - `test_runbook_pins_command_file_validator_as_non_executing_gate` —— 强制 "validates without executing"、禁止 `rm` / `ssh` / `python -c` / `export`、安全 path token 集、`--confirm-cutover` 显式禁
  - `test_runbook_pins_source_target_env_name_allowlist_before_operator_commands` —— `--source-url-env` / `--target-url-env` 必须匹配 `[A-Z_][A-Z0-9_]*`，在任何 env-file 被 source 前校验
- **状态**：✅ 契约绿
- **Stop 语义**：runbook 不得简化、不得允许危险操作行、不得跳过 precheck

#### Surface 3 — synthetic drill runtime report

- **文件**：`src/yuantus/scripts/tenant_import_rehearsal_synthetic_drill.py`
- **契约**：`test_synthetic_drill_runtime_contract_keeps_real_gates_closed`
- **状态**：✅ 契约绿
- **断言**：报告字段永远 `synthetic_drill=true` / `real_rehearsal_evidence=false` / `db_connection_attempted=false` / `ready_for_*=false` / `ready_for_cutover=false`
- **Stop 语义**：synthetic 模式永远不能伪装成真证据

#### Surface 4 — synthetic drill source imports

- **文件**：`src/yuantus/scripts/tenant_import_rehearsal_synthetic_drill.py`（**源代码静态扫描**）
- **契约**：`test_synthetic_drill_source_does_not_call_real_archive_or_handoff_gates`
- **状态**：✅ 契约绿
- **断言**：源码字符串里**不**得出现 `tenant_import_rehearsal_evidence_archive` 或 `tenant_import_rehearsal_evidence_handoff`
- **Stop 语义**：synthetic 模式即使在运行时也不能调用真实 evidence chain 模块

#### Surface 5 — synthetic drill design & verification docs

- **文件**：
  - `docs/DEVELOPMENT_CLAUDE_TASK_PHASE3_TENANT_IMPORT_SYNTHETIC_DRILL_20260429.md`（design）
  - `docs/DEV_AND_VERIFICATION_PHASE3_TENANT_IMPORT_SYNTHETIC_DRILL_20260429.md`（verification）
- **契约**：`test_design_and_verification_docs_state_external_evidence_remains_missing`
- **状态**：✅ 契约绿
- **断言**：design 含 `real_rehearsal_evidence=false` + `ready_for_evidence_handoff=false`；verification 含 "operator-run PostgreSQL rehearsal evidence is still missing"
- **Stop 语义**：设计与验证文档不得被改写到看似允许 synthetic = evidence

### 5.3 额外 surface（5-surface 框架未单独计但契约存在）

| Surface | 文件 | 契约函数 | 状态 |
|---|---|---|---|
| readiness status（运行+加固说明） | `docs/PHASE3_TENANT_IMPORT_READINESS_STATUS_20260430.md` | `test_readiness_status_keeps_operator_safety_hardening_db_free_and_blocked` | ✅ |
| readiness status TODO（每项加固标 "local safety only"） | `docs/PHASE3_TENANT_IMPORT_READINESS_STATUS_TODO_20260430.md` | `test_readiness_status_keeps_operator_safety_hardening_db_free_and_blocked`（同上后半） | ✅ |
| readiness status external block 标记 | 同上 | `test_readiness_status_preserves_external_blocked_state` | ✅ |

### 5.4 Stop-gate 整体结论

5 surface（+ 3 补强 surface）**全部契约绿**。任何后续 PR 想绕开 P3.4 真证据 gate，必须先击穿这 8 个测试函数之一 —— 这是 P3 范围内**最强**的反退化保护。

---

## 6. 盘点支柱 #5 — Cutover Prerequisite Catalog

> 来源：综合 `PHASE3_TENANT_IMPORT_READINESS_STATUS_20260430.md` §4 / `PHASE3_TENANT_IMPORT_REHEARSAL_TODO_20260427.md` §"Stop Gate" / `DEVELOPMENT_CLAUDE_TASK_PHASE3_TENANT_ALEMBIC_PROVISIONING_20260427.md` §9 / `PHASE3_TENANT_IMPORT_EXTERNAL_EVIDENCE_HANDOFF_PACKET_20260511.md` §3。

### 6.1 输入侧（必须人 / 配置）

| # | 项目 | 来源 | 状态 |
|---|---|---|---|
| 1 | 批准的 pilot tenant id | operator | ⏳ pending 提供 |
| 2 | 非生产 PG **源** DSN（仓库外） | operator | ⏳ pending 提供 |
| 3 | 非生产 PG **目标** DSN（仓库外） | operator | ⏳ pending 提供 |
| 4 | backup/restore owner（命名） | operator | ⏳ pending 提供 |
| 5 | rehearsal window（已排时间） | operator + reviewer | ⏳ pending 提供 |
| 6 | 签字的 table classification artifact | operator | ⏳ pending 提供 |
| 7 | 已命名的 evidence reviewer | operator | ⏳ pending 提供 |

### 6.2 仓库侧前置（必须工件 / 报告）

| # | 项目 | 工件 | 来源 / 守门字段 |
|---|---|---|---|
| 1 | P3.3.1 + P3.3.2 merged + post-merge smoke 绿 | 见 §3 | ✅ 已满足 |
| 2 | P3.4.1 dry-run report `ready_for_import=true` | `tenant_<id>_import_plan.json` | gate 字段在 `tenant_import_rehearsal_plan.py` |
| 3 | source preflight `ready_for_importer_source=true` | `tenant_<id>_source_preflight.json` | `tenant_import_rehearsal_source_preflight.py` |
| 4 | target preflight `ready_for_importer_target=true` | `tenant_<id>_target_preflight.json` | `tenant_import_rehearsal_target_preflight.py` |
| 5 | Claude implementation packet `ready_for_claude_importer=true` | `tenant_<id>_importer_implementation_packet.json` | `tenant_import_rehearsal_implementation_packet.py` |
| 6 | next-action report `claude_required=true` | `tenant_<id>_next_action.json` | `tenant_import_rehearsal_next_action.py` |
| 7 | row-copy rehearsal 报告 `import_executed=true` | `tenant_<id>_rehearsal_report.{json,md}` | `tenant_import_rehearsal.py` |
| 8 | operator evidence Markdown（**真实**签字字段） | operator-run；模板由 `tenant_import_rehearsal_evidence.py` 生成 | 非占位符 |
| 9 | archive manifest（artifact hash） | `tenant_<id>_archive_manifest.json` | `tenant_import_rehearsal_evidence_archive.py` |
| 10 | redaction guard 全 artifact 覆盖 | `tenant_<id>_redaction_guard.json` | `tenant_import_rehearsal_redaction_guard.py` |
| 11 | evidence intake `ready_for_evidence_intake=true` | `tenant_<id>_evidence_intake.json` | `tenant_import_rehearsal_evidence_intake.py` |
| 12 | evidence handoff `ready_for_evidence_handoff=true` | `tenant_<id>_evidence_handoff.json` | `tenant_import_rehearsal_evidence_handoff.py` |
| 13 | reviewer packet `ready_for_reviewer_packet=true` | `tenant_<id>_reviewer_packet.json` | `tenant_import_rehearsal_reviewer_packet.py` |
| 14 | external evidence handoff packet 已 review | `docs/PHASE3_TENANT_IMPORT_EXTERNAL_EVIDENCE_HANDOFF_PACKET_20260511.md` | ✅ 已提供 |
| 15 | external evidence reviewer checklist 已签 | `docs/PHASE3_TENANT_IMPORT_EXTERNAL_EVIDENCE_REVIEW_CHECKLIST_20260511.md` | ✅ 已提供 |
| 16 | reviewer 接受决策（接受真证据） | reviewer 在 checklist 上签 | ⏳ pending |
| 17 | **所有上述报告 `ready_for_cutover=false`** | 所有 json 工件 | 永久契约 |

### 6.3 安全侧前置（local hardening contracts 必须绿）

per `PHASE3_TENANT_IMPORT_READINESS_STATUS_20260430.md` §5 reviewer checklist 末段：

- env-file precheck / command-file validator / wrapper safety contracts 绿
- env-file key allowlist 对 command-pack + full-closeout 包装器都覆盖
- command-file 可执行行 allowlist 覆盖
- command-file 选项行 allowlist 覆盖
- command-file safe path option 对重定向 / 变量展开 / quoted path rewrite 都覆盖
- command-file quoted metadata 对 shell 变量展开 / backslash escape 都覆盖
- command-file shell syntax 诊断 redaction 覆盖
- command-file validator CLI error redaction 覆盖
- env-file precheck CLI error redaction 覆盖
- shell wrapper CLI error redaction 覆盖
- Python module CLI error redaction 覆盖

✅ 全部本地加固已合并（见 §2.2 表）；CI 持续验证。

### 6.4 Canonical Operator Path（5 步）

per `PHASE3_TENANT_IMPORT_EXTERNAL_EVIDENCE_HANDOFF_PACKET_20260511.md` §4：

```bash
# 1. 生成 repo-external env-file 模板
scripts/generate_tenant_import_rehearsal_env_template.sh \
  --out "$HOME/.config/yuantus/tenant-import-rehearsal.env"

# 2. （仓库外）编辑 env-file 填真 DSN

# 3. precheck env-file（**任何** shell source 或 DB 动作之前）
scripts/precheck_tenant_import_rehearsal_env_file.sh \
  --env-file "$HOME/.config/yuantus/tenant-import-rehearsal.env"

# 4. 在已批准 rehearsal window 内跑 full-closeout 包装器
scripts/run_tenant_import_rehearsal_full_closeout.sh \
  --implementation-packet-json output/tenant_<tenant-id>_importer_implementation_packet.json \
  --artifact-prefix output/tenant_<tenant-id> \
  --backup-restore-owner "<owner>" \
  --rehearsal-window "<window>" \
  --rehearsal-executed-by "<operator>" \
  --evidence-reviewer "<reviewer>" \
  --date "<yyyy-mm-dd>" \
  --env-file "$HOME/.config/yuantus/tenant-import-rehearsal.env" \
  --confirm-rehearsal \
  --confirm-closeout

# 5. review evidence-intake / evidence-handoff / reviewer-packet 工件
```

### 6.5 完成定义（Phase 5 解锁条件）

P3.4 **完成** = 上面 §6.1 + §6.2 全部满足 + reviewer 在 checklist 上签字接受真证据。

Phase 5 **解锁** = P3.4 完成 + 一次显式 "signoff PR" 把接受的 evidence 工件摘要（hash / reviewer 签名）记入仓库，**同时保持** `ready_for_cutover=false`。

---

## 7. Open Questions（需要外部决策）

| # | 问题 | 现状 / 选项 |
|---|---|---|
| 1 | 是否在 P3.4 cutover 之前落 P3.3.3 baseline revision（首个 tenant 表 autogenerate 修订） | 由 operator 在 P3.4 stop-gate review 决定；选项 A：单独 sub-PR 提前落；选项 B：与 P3.4 cutover 同期落，使表集快照贴近 cutover 时间。**P3.3 taskbook §3.3 推荐 operator 自决** |
| 2 | pilot tenant id 选择 | 待 operator 命名；建议小规模 + 业务上可暂停的 tenant，便于回滚 |
| 3 | rehearsal window 与生产维护窗口的关系 | 待 operator 与 reviewer 排期；rehearsal **不**触生产 DB，理论上可独立排，但要 backup/restore owner 在场 |
| 4 | evidence reviewer 是 operator 同事还是独立第三方 | 待批；handoff packet 要求 "named reviewer"，未规定独立性级别 |
| 5 | reviewer 接受后是否再做一次 dry-run | 不强制；但若 rehearsal 与 cutover 之间相隔较久，建议在 cutover 前再跑一次 source preflight + dry-run |
| 6 | reviewer 拒收的回滚路径 | 已通过；evidence chain 不 mutate 仓库；rehearsal 工件在外部 artifact 区，可丢弃；下一轮重做。**无**自动回滚 / 无 destructive cleanup（per §1 contracts） |
| 7 | 多 pilot tenant 并行 vs 串行 | runbook 第 7.1/7.2 节按单 tenant 写；多 tenant 应**串行**，每个独立 evidence chain；并行未在 R3 范围 |
| 8 | 工件存储位置 | handoff packet 要求 "repo-external"；具体路径（`$HOME/.config/yuantus/`、企业网盘、加密存档）由 operator 决定 |
| 9 | Tier-B #3 closeout 后是否影响 P3.4 评估口径 | **不影响**。Tier-B 是 breakage design loopback portfolio（与 odoo18 相关），P3.4 是 schema-per-tenant 多租户路径；两者独立。本 taskbook 仍以 P3.4 自身 anchor 为准 |

---

## 8. Hard Non-Goals（本 taskbook + P3.4 范围共同的不可破红线）

### 8.1 本 taskbook 范围 non-goals

- **不**新增任何 P3.4 实现资源
- **不**编辑 schema / runtime / `database.py` / `migrations*/env.py` / `tenant_schema.py` / `tenant_import_rehearsal_*`
- **不**编辑或新增 operator pack 脚本（`scripts/*.sh`）
- **不**接 PostgreSQL；不开 DSN；不读 token / 凭据
- **不**生成、不接受、不验证 evidence artifact
- **不**修改任何 stop-gate contract 或 acceptance boundary

### 8.2 P3.4 框架本身的 non-goals（即使工作恢复进行也不变）

| 红线 | 来源 |
|---|---|
| 不在任何环境启用 `TENANCY_MODE=schema-per-tenant` | P3.2 / P3.3 / P3.4 各 taskbook |
| 不做 production cutover | 全部 P3 文档 |
| 不在生产 DB 做 data import | 同上 |
| 不做自动 rollback / 不做 destructive cleanup | `PHASE3_TENANT_IMPORT_REHEARSAL_TODO_20260427.md` §"Explicitly Not Started" |
| 不接受 synthetic drill 输出作为真证据 | `PHASE3_TENANT_IMPORT_EXTERNAL_EVIDENCE_HANDOFF_PACKET_20260511.md` §6 |
| 不接受 mock DSN | 同上 |
| 不接受含明文 PG 密码的 artifact | 同上 |
| 不接受标 `Ready for cutover: true` 的 artifact | 同上 |
| Phase 5 不在 evidence 被接受 + signoff PR 落地之前启动 | `DEV_AND_VERIFICATION_NEXT_CYCLE_POST_P3_4_READINESS_PLAN_REFRESH_20260511.md` |
| `DROP SCHEMA` 永远不出现在 P3.3 / P3.4 范围 | `DEVELOPMENT_CLAUDE_TASK_PHASE3_TENANT_ALEMBIC_PROVISIONING_20260427.md` §11 |
| 不允许仓库内本地工具假装关闭外部 evidence gate | `test_tenant_import_rehearsal_stop_gate_contracts.py` 全 8 个测试 |
| `migrations/env.py` + `migrations_identity/env.py` 在 P3.4 范围内零改动 | 同上 |

### 8.3 反例（如果未来 PR 出现以下任一即应拒收）

- 把 `- [ ] Add operator-run PostgreSQL rehearsal evidence.` 改为 `- [x]` 但没附 evidence artifact 摘要 + reviewer 接受决策
- 任何 evidence json 含 `ready_for_cutover: true`
- synthetic drill 模块 import 了 archive / handoff 模块（违反 Surface 4）
- runbook 删除 / 简化 "synthetic drill is not operator-run evidence" 警告（违反 Surface 2）
- 在 P3.4 范围 PR 里夹带 `migrations/env.py` 或 `migrations_identity/env.py` 改动（违反 P3.3 边界）
- P3.4 PR 顺手启用 `TENANCY_MODE=schema-per-tenant`

---

## 9. 本 taskbook 自身验收

per `feedback_p3_4_readiness_taskbook_trigger.md`：

- ✅ doc-only（本文件是唯一新增/修改项）
- ✅ read-only 盘点（仅 `Read` + `grep` + `git log`；无 schema / runtime / operator-pack 编辑）
- ✅ 覆盖 7 项契约要求：tenant import 证据 / provisioning 证据 / rehearsal 证据 / 5 stop-gate 状态 / cutover prerequisite catalog / open questions / hard non-goals
- ✅ 未开实现分支（当前 `design/p3-4-readiness-taskbook-20260519` 是 design 分支）
- ✅ 未触 `src/`、`migrations*/`、`scripts/`、`alembic*.ini`

完成本盘点**不**等于授权下一阶段（per `feedback_phase_optin.md`）。下一步（任何 P3.4 实际推进）需要单独 opt-in。

---

## 10. 下一步可能选项（**仅供你决策参考，未授权执行**）

- **A**：等待外部 operator 跑真实非生产 PG rehearsal、产生 evidence、reviewer 接受 —— 这是**唯一**可解锁 Phase 5 的路径
- **B**：在 evidence 缺失下继续仓库内安全加固（任何加固必须在 contracts 中显式标 "local safety only, does not close external gate"）
- **C**：处理 Open Questions #1（P3.3.3 baseline revision 提前 vs 与 cutover 同期）—— 这是一次独立设计稿/决策
- **D**：其他（你提）

每条都需要独立 opt-in 才进。

---

## 11. 文件清单（本 taskbook 引用的关键 anchor）

| 类别 | 文件 |
|---|---|
| readiness 主台账 | `docs/PHASE3_TENANT_IMPORT_READINESS_STATUS_20260430.md` |
| readiness TODO | `docs/PHASE3_TENANT_IMPORT_READINESS_STATUS_TODO_20260430.md` |
| 父 TODO | `docs/PHASE3_TENANT_IMPORT_REHEARSAL_TODO_20260427.md` |
| 运维 runbook | `docs/RUNBOOK_TENANT_MIGRATIONS_20260427.md` |
| stop-gate contracts 文档 | `docs/DEV_AND_VERIFICATION_PHASE3_TENANT_IMPORT_STOP_GATE_CONTRACTS_20260430.md` |
| stop-gate contracts 测试 | `src/yuantus/tests/test_tenant_import_rehearsal_stop_gate_contracts.py` |
| synthetic drill 设计 | `docs/DEVELOPMENT_CLAUDE_TASK_PHASE3_TENANT_IMPORT_SYNTHETIC_DRILL_20260429.md` |
| synthetic drill 验证 | `docs/DEV_AND_VERIFICATION_PHASE3_TENANT_IMPORT_SYNTHETIC_DRILL_20260429.md` |
| external evidence handoff packet | `docs/PHASE3_TENANT_IMPORT_EXTERNAL_EVIDENCE_HANDOFF_PACKET_20260511.md` |
| external evidence reviewer checklist | `docs/PHASE3_TENANT_IMPORT_EXTERNAL_EVIDENCE_REVIEW_CHECKLIST_20260511.md` |
| post-P6 plan refresh | `docs/DEV_AND_VERIFICATION_NEXT_CYCLE_POST_P3_4_READINESS_PLAN_REFRESH_20260511.md` |
| 主 TODO 计划 | `docs/DEVELOPMENT_NEXT_CYCLE_TODO_PLAN_20260426.md` |
| P3.3 taskbook | `docs/DEVELOPMENT_CLAUDE_TASK_PHASE3_TENANT_ALEMBIC_PROVISIONING_20260427.md` |
| provisioning helper | `src/yuantus/scripts/tenant_schema.py` |
| tenant Alembic env | `migrations_tenant/env.py` |
| tenant import 入口 | `src/yuantus/scripts/tenant_import_rehearsal.py` |

---

## 12. 变更记录

| 版本 | 日期 | 变更 |
|---|---|---|
| R1 | 2026-05-19 | 初稿。doc-only 盘点；触发口令 `Go P3.4 readiness taskbook` 授权范围内。`main=dd88ea2` |
