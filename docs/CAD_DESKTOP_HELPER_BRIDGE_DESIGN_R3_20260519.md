# CAD Desktop Helper Bridge — Design R3 (2026-05-19)

**Status**: Design draft, awaiting implementation opt-in（R3 — Codex 第二轮审阅修订全部纳入；R1 / R2 文档作废，本稿为唯一参考版本）
**Scope**: Step 1（Windows CAD 环境探测器） + Step 2（`yuantus-cad-helper` localhost HTTP 桥）
**Out of scope this revision**: Tauri Companion UI、HKCU 文件关联落盘、Vault 同步、3D Viewer、BOM 桌面视图、SolidWorks/Inventor/CATIA/NX/Creo 适配、国产 CAD R3 阶段写 DWG、helper 暴露服务端 values/target_properties/target_cad_fields 等非 item_id 路径。

R3 在 R2 基础上修复以下问题（详见 §11 变更记录）：
- **High**：.NET 目标框架与 AutoCAD 2018 v4.6 基线对齐（Shared 多目标 net46+net6.0；Bridge 走 .NET Framework v4.6；NETLOAD 装载必须为完整 .NET Framework 程序集）
- **High**：恢复 `/sync/inbound` 和 `/sync/outbound`，保证 `PLMMATPUSH` 命令不退化
- **Medium**：`item_id` 强制为 helper 层范围约束，服务端并未硬约束
- **Medium**：补完 local token bootstrap 与 reset 流程；`--reset-local-token` 仅本机交互式执行，不接受 HTTP / 远程触发

---

## 1. 背景

### 1.1 现状

- Yuantus 已有完整的 AutoCAD .NET DLL Bundle 插件（`clients/autocad-material-sync/CADDedupPlugin/`），部署到 `%APPDATA%\Autodesk\ApplicationPlugins\CADDedup.bundle`，免管理员安装。**目标框架按 AutoCAD 版本切换**：AutoCAD 2018 → `TargetFrameworkVersion = v4.6`；AutoCAD 2024 → `TargetFrameworkVersion = v4.8`（依据 `CADDedupPlugin.csproj:16-17`）。
- 服务端 `plugins/yuantus-cad-material-sync/main.py`（2490 行）已经实装 profile / compose / diff / sync inbound + outbound + 配置导入导出 + 灰度版本。**关键事实**：
  - `CadDiffPreviewRequest.item_id` 是 **可选**（`main.py:381-389`），并支持 `values` / `target_properties` / `target_cad_fields` 等其他路径。R3 helper 出于范围收敛**仅在 helper 层强制 item_id**，**不**修改服务端契约。
  - `CadDiffPreviewResponse.write_cad_fields` 字段已存在（`main.py:392-407`）；契约就按"服务端只下发写指令、客户端在 CAD 进程内写"设计。
  - `/sync/inbound` 与 `/sync/outbound` 是合法实存端点（`main.py:407-450`），分别对应 CAD 字段 → PLM 物料入站、PLM 物料 → CAD 字段拉取。
- 现有 .NET 客户端 `MaterialSyncApiClient` 已经使用 `SyncInboundAsync`（`:90` POST `/sync/inbound`）、`SyncOutboundAsync`（`:124` POST `/sync/outbound`）、`DiffPreviewAsync`（`:128` POST `/diff/preview`）；`DedupPlugin.cs:535` 的 `[CommandMethod("PLMMATPUSH")]` 直接调用 `SyncInboundAsync`。
- 服务端 `/auth/login` 必填 `tenant_id`、可选 `org_id`（`src/yuantus/api/routers/auth.py:19-23`）；`MaterialSyncApiClient.cs:147-156` 默认头注入 `x-tenant-id` / `x-org-id`。R3 helper 必须沿用同样的多租户语义。
- 无桌面伴侣（无 Tauri/Electron 壳）。仅 AutoCAD 一套客户端落地；SolidWorks 仅到 R1 骨架（`docs/DEV_AND_VERIFICATION_CAD_MATERIAL_SYNC_SOLIDWORKS_CLIENT_SKELETON_R1_20260512.md`）；国产 CAD（ZWCAD、GstarCAD）完全空缺。

### 1.2 目标

**Step 1 — 环境探测器**：在不写注册表、不复制文件的前提下，扫描当前 Windows 主机上的 CAD 安装情况，输出可被安装向导/Tauri Companion/CI 消费的 JSON 报告。

**Step 2 — `yuantus-cad-helper` 桥**：提供一个轻量、单机回环的 HTTP 服务，作为 CAD 内（.NET 插件或 LISP）与外部桌面工具的唯一汇聚点。helper 本身**不写 DWG**、**不引入新服务端 API**，只做 transport + 鉴权 + 审计。DWG 字段写回由 AutoCAD 进程内的 `CADDedupPlugin` 完成；国产 CAD R3 阶段不写 DWG，仅做 diff 展示。

### 1.3 非目标

- 不替换已有的 `CADDedupPlugin`；helper 作为新增并行进程存在，AutoCAD 端的 DWG 读写逻辑全部保留在 `CADDedupPlugin` 内。
- 不引入 Tauri/Electron 壳（独立稿）。
- 不写任何注册表或文件关联条目（detector 严格只读、helper 仅写 `%APPDATA%\YuantusPLM\` 自有目录）。
- 不接管 SolidWorks/Inventor/CATIA/NX/Creo（独立稿）。
- 不给 ZWCAD/GstarCAD 写 DWG —— R3 国产 CAD 仅做：探测 + LISP 瘦壳安装 + helper 调用 + diff 展示。写回适配放下一阶段，需独立真机验证。
- **不暴露服务端 `/diff/preview` 的 values / target_properties / target_cad_fields 等非 item_id 路径** —— 这些服务端能力存在但 R3 helper 不暴露；R3+ 评估是否暴露。
- 不引入新服务端 API。
- helper 不提供 HTTP / 远程触发的 token 重置；token 重置仅通过本机交互式 `--reset-local-token` 命令完成。

### 1.4 与 Codex 修订版优先级的对齐

| Codex Step | R3 覆盖 |
|---|---|
| 1. Windows CAD 环境探测器（只读） | ✅ 第 4 节 |
| 2. `yuantus-cad-helper` 桥接入口 | ✅ 第 5 节 |
| 3. ZWCAD/GstarCAD LISP 瘦插件 | ⏳ R3 给出协议规范 + LISP 适配 DLL 设计（§5.7），插件本身另起独立稿 |
| 4. Tauri Desktop Companion | ⏳ 独立设计稿 |
| 5. HKCU 文件关联落盘 | ⏳ 独立设计稿 |
| 6–8. Viewer / BOM / Vault | ⏳ 独立设计稿 |

---

## 2. 架构总览

```
┌──────────────────────────────────────────────────────────────────────┐
│                         User Workstation                              │
│                                                                       │
│  AutoCAD 路线（强插件）              国产 CAD 路线（瘦桥接，R3 不写 DWG）│
│  ┌────────────────────────┐         ┌─────────────────────────────┐  │
│  │ acad.exe               │         │ ZWCAD.exe / gscad.exe       │  │
│  │ ┌────────────────────┐ │         │ ┌─────────────────────────┐ │  │
│  │ │ CADDedup.bundle    │ │         │ │ LISP 瘦壳               │ │  │
│  │ │ (.NET Framework    │ │         │ │ PLM_DIFF_PREVIEW etc.   │ │  │
│  │ │  v4.6 / v4.8)      │ │         │ │ ↓ NETLOAD               │ │  │
│  │ │ ┌────────────────┐ │ │         │ │ YuantusCadHelper        │ │  │
│  │ │ │ CadMaterial    │ │ │         │ │ Bridge.dll              │ │  │
│  │ │ │ FieldService   │ │ │         │ │ (.NET Framework v4.6)   │ │  │
│  │ │ │  (DWG 读写)    │ │ │         │ │ (yuantus-helper-call)   │ │  │
│  │ │ └────────────────┘ │ │         │ └────────────┬────────────┘ │  │
│  │ │       ↑            │ │         │              │              │  │
│  │ │ ┌─────┴──────────┐ │ │         │              │              │  │
│  │ │ │ Yuantus.Cad.   │◀┼─┼──┐  ┌──▶│  ──────► 引用 Shared        │  │
│  │ │ │ Shared (net46 │ │ │  │  │   │              │              │  │
│  │ │ │  multi-target) │ │ │  │  │   └──────────────┼──────────────┘  │
│  │ │ └────────────────┘ │ │  │  │                  │                 │
│  │ └────────────────────┘ │  │  │                  │ HTTP            │
│  └────────────┬───────────┘  │  │                  ▼                 │
│               │              │  │   ┌────────────────────────────┐   │
│               │ HTTP         │  │   │ yuantus-cad-helper.exe     │   │
│               │ 127.0.0.1    │  │   │ (.NET 6 self-contained,    │   │
│               └──────────────┼──┴──▶│  singleton, loopback only) │   │
│                              │      │ 端口：7959 起线性分配       │   │
│                              │      │ 鉴权：本地 token + PID 白名单│  │
│                              │      │ 审计：%APPDATA%\YuantusPLM\ │   │
│                              │      │       audit.db              │   │
│                              └──────│                            │   │
│                                     └─────────────┬──────────────┘   │
│                                                   │ HTTPS            │
│                                                   ▼                  │
│                                       ┌──────────────────────┐       │
│  yuantus-cad-detector.exe             │ Yuantus PLM API       │      │
│  (.NET 6 self-contained,              │ /auth/login           │      │
│   one-shot, read-only, no writes)     │ /plugins/cad-         │      │
│   └─▶ 也引用 Shared (net6.0 target)   │   material-sync/...   │      │
│                                       └──────────────────────┘       │
└──────────────────────────────────────────────────────────────────────┘
```

**三层组件职责**：

| 层 | 名称 | 目标框架 | 职责 |
|---|---|---|---|
| Transport 共享层 | `Yuantus.Cad.Shared` | **多目标：`net46;net6.0`** | helper 进程发现（读 `helper-session-{sessionId}.json`）、helper 自动启动、DPAPI token 读写、HTTP 同步调用、错误信封解码、注册表抽象、`install-id.json` 原子读写 |
| AutoCAD 路线 | `CADDedupPlugin` | v4.6（2018）/ v4.8（2024）多 config | 引用 Shared 的 **net46** target 取代原 `MaterialSyncApiClient` 的直连 HTTP；继续承担 DWG 读写、diff 窗口、PLMMAT* 命令、`/audit/apply-result` 上报 |
| 国产 CAD 路线 | `YuantusCadHelperBridge.dll` | **.NET Framework v4.6**（NETLOAD 必须完整 .NET Framework） | 引用 Shared 的 **net46** target；仅对外暴露 `(yuantus-helper-call endpoint json) → json` LISP 函数；不写 DWG、不解析业务 JSON |
| 桌面服务 | `yuantus-cad-helper.exe` | **.NET 6 self-contained** | Kestrel loopback；端点实现；SQLite 审计 |
| 桌面探测器 | `yuantus-cad-detector.exe` | **.NET 6 self-contained** | 注册表 + 文件系统扫描；JSON 输出 |

**关键约束**：
- helper 是 **外部进程**，永远不持有 AutoCAD `Document` 上下文，**永远不写 DWG**。所有 DWG 写入只在 CAD 进程内由 `CADDedupPlugin`（或国产 CAD 后续阶段的专用适配）完成。
- helper 是 **唯一** 与 PLM 服务端通信的本机出口。CAD 端两个客户端 DLL 都不直接打 PLM。
- LISP 永不直接发 HTTP、永不读 DPAPI、永不持有 token。
- `CADDedupPlugin` **不**通过 LISP 桥调用 helper —— 它是 C# 进程内直接 `ProjectReference` `Yuantus.Cad.Shared`，直接方法调用。LISP 桥只为国产 CAD 服务。
- **netstandard2.0 不在 R3 范围**：.NET Framework v4.6 不在 netstandard2.0 官方兼容矩阵（需 v4.6.1+），Shared 走多目标 `net46;net6.0` 而非 netstandard。

---

## 3. 技术选型

| 组件 | 推荐 | 备选 | 理由 |
|---|---|---|---|
| detector 实现语言 | **.NET 6 / C#**（self-contained 单 exe） | PowerShell（仅限本地诊断） | 与 helper 同栈；可共享 Shared 中的注册表抽象 |
| helper 实现语言 | **.NET 6 / C#** + `Kestrel`（仅绑 loopback） | Rust + `axum` | 同栈复用；Rust 留作后续优化备选 |
| **`Yuantus.Cad.Shared` 目标框架** | **`<TargetFrameworks>net46;net6.0</TargetFrameworks>`** | netstandard2.0（**已排除**） | AutoCAD 2018 基线 v4.6 不在 netstandard2.0 官方支持矩阵；多目标比拆双库更紧凑且共享源码 |
| **`YuantusCadHelperBridge.dll` 目标框架** | **`<TargetFrameworkVersion>v4.6</TargetFrameworkVersion>`** | netstandard2.0（**已排除**） | NETLOAD 进 acad.exe / ZWCAD.exe 必须为完整 .NET Framework 程序集；以 AutoCAD 2018 v4.6 基线为最低共同分母 |
| `CADDedupPlugin` 目标框架 | 保持现有 `v4.6`（2018）/`v4.8`（2024）多 config | — | 既有项目结构，零改动 |
| 进程间发现 | `%APPDATA%\YuantusPLM\helper-session-{sessionId}.json`（per-session）+ `%APPDATA%\YuantusPLM\install-id.json`（per-user-per-machine，原子 CreateNew） | Named Pipe | 跨技术栈兼容；LISP / Tauri / PowerShell 都能读 |
| 本地 token 存储 | Windows DPAPI 用户作用域 + 固定 entropy | Credential Manager（也是 DPAPI 后端） | 跨进程同用户可读；不防同用户其他进程 —— 见 §5.3 |
| 日志 | `Serilog` → `%APPDATA%\YuantusPLM\logs\helper-YYYY-MM-DD.log` 每日轮转 | NLog | 与既有 .NET 团队习惯一致 |
| 本地审计 | SQLite via `Microsoft.Data.Sqlite` | LiteDB | 单文件、跨工具可读 |

---

## 4. Step 1 — CAD 环境探测器

### 4.1 命令行接口

```
yuantus-cad-detector.exe [--output <path>] [--format json|table] [--verbose]
```

- 默认输出 `stdout`，`--output` 覆盖到文件。
- `--format table` 用于人工运维（终端表格），不建议被其他程序解析。
- 退出码：`0` = 至少检出一个受支持 CAD；`2` = 未检出但运行成功；`64` = 非 Windows；`1` = 内部错误。
- **不接受**任何"安装 / 修复 / 写入"开关 —— R3 严格只读。

### 4.2 探测目标矩阵

| 厂商 | 产品 | 注册表根 | R-Key → 营销版本映射 | 备注 |
|---|---|---|---|---|
| Autodesk | AutoCAD | `HKLM\SOFTWARE\Autodesk\AutoCAD\<R*.*>\<CLSID>` | R22.0=2018, R23.0=2019, R23.1=2020, R24.0=2021, R24.1=2022, R24.2=2023, R24.3=2024, R25.0=2025 | CLSID 子键含 `ProductName/AcadLocation/Language/ProductRoot` |
| Autodesk | AutoCAD Plant 3D | 同上，过滤 `ProductName LIKE 'AutoCAD Plant 3D%'` | 同上 | Plant 3D 复用 AutoCAD R-Key |
| ZWSOFT | ZWCAD | `HKLM\SOFTWARE\ZWSOFT\ZWCAD\<年份>` | 直接取键名 | 年份键即营销版本 |
| Gstarsoft | GstarCAD | `HKLM\SOFTWARE\Gstarsoft\GstarCAD\<年份>` | 同上 | |
| Dassault | SolidWorks | `HKLM\SOFTWARE\SolidWorks\SOLIDWORKS\<年份>` | 同上 | R3 仅探测，不安装 |

WOW64 注意：64 位 Windows 必须同时扫 `HKLM\SOFTWARE` 与 `HKLM\SOFTWARE\WOW6432Node`。

### 4.3 探测策略

```
for vendor in [Autodesk, ZWSOFT, Gstarsoft, Dassault]:
    for hive in [HKLM\SOFTWARE, HKLM\SOFTWARE\WOW6432Node]:
        if registry-key-exists(hive\vendor):
            enumerate-subkeys → collect (product, version, install_root, exe_path, language)
        else:
            continue  # 安静跳过，不视为错误

for each detected product:
    if exe_path 不存在:
        status = "registry-orphan"
    elif yuantus_bundle_dir 存在:
        从 PackageContents.xml 取 bundle 版本
        status = "supported" | "bundle-mismatch"
    else:
        status = "supported-no-bundle"
```

**关键不变量**：
- 不抛异常退出。所有权限不足 / 键缺失都降级为该条目的 `errors[]` 字段。
- 不区分大小写。
- 64-bit / 32-bit 注册表视图都扫，去重以 `install_root` 规范化路径为准。

### 4.4 输出 JSON Schema

```json
{
  "schema_version": "1.0",
  "scanned_at": "2026-05-19T11:30:00+08:00",
  "host": {
    "os": "Windows 11 Pro 23H2",
    "arch": "x64",
    "username": "frank",
    "is_admin": false
  },
  "products": [
    {
      "id": "autocad-2018-zh-cn",
      "vendor": "Autodesk",
      "product": "AutoCAD",
      "release_key": "R22.0",
      "marketing_version": "2018",
      "language": "zh-CN",
      "install_root": "C:\\Program Files\\Autodesk\\AutoCAD 2018",
      "exe_path": "C:\\Program Files\\Autodesk\\AutoCAD 2018\\acad.exe",
      "support_dirs": [
        "C:\\Program Files\\Autodesk\\AutoCAD 2018\\Support",
        "C:\\Users\\frank\\AppData\\Roaming\\Autodesk\\AutoCAD 2018\\R22.0\\enu\\Support"
      ],
      "plugin_bundle_dirs": [
        "C:\\Users\\frank\\AppData\\Roaming\\Autodesk\\ApplicationPlugins",
        "C:\\ProgramData\\Autodesk\\ApplicationPlugins"
      ],
      "yuantus_bundle": {
        "present": true,
        "path": "C:\\Users\\frank\\AppData\\Roaming\\Autodesk\\ApplicationPlugins\\CADDedup.bundle",
        "package_version": "0.1.0",
        "supports_release": ["R22.0", "R24.3"]
      },
      "compatibility": "supported",
      "errors": []
    },
    {
      "id": "zwcad-2024",
      "vendor": "ZWSOFT",
      "product": "ZWCAD",
      "marketing_version": "2024",
      "install_root": "C:\\Program Files\\ZWSOFT\\ZWCAD 2024",
      "exe_path": "C:\\Program Files\\ZWSOFT\\ZWCAD 2024\\ZWCAD.exe",
      "yuantus_bundle": { "present": false },
      "compatibility": "experimental",
      "errors": []
    }
  ],
  "recommendations": [
    {
      "level": "info",
      "code": "HELPER_NOT_INSTALLED",
      "message": "未发现 yuantus-cad-helper；如需多 CAD 桥接请由 Tauri Companion 引导安装。"
    }
  ],
  "warnings": []
}
```

### 4.5 兼容性等级（`compatibility` 字段）

| 等级 | 语义 |
|---|---|
| `supported` | R3 已正式回归验证（AutoCAD R22.0 / R24.3） |
| `supported-no-bundle` | 受支持但 Yuantus bundle 未安装 |
| `bundle-mismatch` | bundle 已装但 PackageContents 不覆盖该 R-Key |
| `experimental` | 探测到但 R3 不保证写 DWG（ZWCAD/GstarCAD/SW） |
| `registry-orphan` | 注册表项残留，可执行体已不存在 |
| `unknown` | 未识别版本（如未来 AutoCAD R25/R26） |

### 4.6 验收测试（Step 1）

**CI 跑**
- 注册表抽象层用 mock：key 缺失 → 安静跳过，不抛
- WOW6432Node 与原生视图同时存在 → 去重生效
- JSON Schema 校验（schema_version + 各字段类型）
- 未知 R-Key（例如 R26.0）→ `marketing_version` 留空，`compatibility = unknown`
- 非 Windows（macOS/Linux）→ 退出码 64

**真机手测（人工验收）**
- Windows 11 + AutoCAD 2018 真机：JSON 中存在 `release_key = "R22.0"` 且 `compatibility = "supported"`
- `yuantus_bundle.present` 与实际安装一致
- `--output` 写文件后 stdout 静默
- **Procmon 录像证明零注册表写**（.pml 存档作为验收证据）

---

## 5. Step 2 — yuantus-cad-helper 桥

### 5.1 进程模型

- 单文件 self-contained .NET 6 exe，部署到 `%APPDATA%\YuantusPLM\helper\yuantus-cad-helper.exe`
- **单例**：启动时尝试创建 `Local\YuantusCadHelper-{installId}` 命名 Mutex（`Local\` 命名空间是 **session-scoped**；详见 §5.1.1 隔离模型）
- 不开机自启。CAD 端在调用 helper API 前如发现进程不在 → 自动 spawn → 轮询 `/healthz` 直到 200（最多 5 秒）
- 空闲超时：默认 30 分钟无请求自动退出，由 `config.json` 中 `idle_timeout_minutes` 覆盖
- 子命令：`yuantus-cad-helper.exe`（默认服务模式）/ `yuantus-cad-helper.exe --reset-local-token`（见 §5.3）
- 当前会话的发现文件命名：`helper-session-{sessionId}.json`（`sessionId = Process.GetCurrentProcess().SessionId`），详见 §5.2

**单例 + 残留恢复流程（R3.2 修订）**：

```
进程 B 启动：
  1. 尝试拿命名 Mutex `Local\YuantusCadHelper-{installId}`
  2a. Mutex 拿到 → 正常启动，写 helper-session-{sessionId}.json，开始服务
  2b. Mutex 已被占 → 进入"已运行验证"流程：
      3. 读当前 session 的 helper-session-{sessionId}.json
         3a. 文件不存在 → wait 500ms 重试步骤 2，最多 3 次；都失败 → 退出码 HELPER_SINGLETON_LOST
         3b. 文件存在 → 读 port + pid + image_path
      4. **裸 GET** http://127.0.0.1:<port>/healthz（**不带** X-Yuantus-Local-Token；500ms 超时；/healthz 本就鉴权与来源双豁免，探活刻意与 token 解耦，避免把"helper 是否活着"和"DPAPI 是否可读"混在一起）
         4a. 200 → 静默退出码 0（说明真在跑，调用方继续用既有端口）
         4b. 非 200 / 超时 / 连接拒绝 → 进入"持有者死活判定"：
             5. 用 helper-session 文件里的 pid 查进程：
                - 进程不存在 → 视为残留（步骤 6）
                - 进程存在但映像路径与 image_path 不匹配 → 视为残留（步骤 6；可能 PID 已被复用）
                - 进程存在且映像路径与发现文件 image_path 字段匹配 → **helper 进程活着但 HTTP 不健康**（死锁 / GC 卡 / Kestrel 异常）→
                    退出码 **HELPER_UNHEALTHY**（**不删** helper-session 文件，避免误删一个仍活着但不健康的 helper 的发现条目）；
                    提示用户："helper 进程 (pid=<N>) 仍在运行但 /healthz 失败，请人工 taskkill 后重试或运行诊断"
             6. 强制删除 helper-session-{sessionId}.json
             7. 回到步骤 1 重试拿 Mutex（前一持有者已死，Mutex 应可获）
             8. 重试 3 次仍拿不到 → 退出码 HELPER_SINGLETON_LOST
```

进程 A 正常退出 / SIGTERM：**必须**删除当前 session 的 `helper-session-{sessionId}.json`。崩溃残留由下次启动的步骤 5/6 兜底。

#### 5.1.1 installId 来源 + 原子生成 + per-user-per-session 隔离模型（R3.2 新增）

**installId 文件**：`%APPDATA%\YuantusPLM\install-id.json`

```json
{ "schema_version": "1.0", "install_id": "550e8400-e29b-41d4-a716-446655440000", "created_at": "2026-05-19T11:30:00+08:00" }
```

**生成必须原子，防止多 CAD 同时首次启动竞态**：

```
helper / Shared 首次需要 installId 时：
  1. 调 FileStream(path, FileMode.CreateNew, FileAccess.Write, FileShare.None)
     1a. 成功 → 在 stream 上写 { install_id: Guid.NewGuid(), ... } 完整 JSON，flush，关闭
     1b. 失败 IOException (ERROR_FILE_EXISTS) → 说明另一进程已经赢了：
         直接进入步骤 2
  2. 用 FileShare.Read 重新打开既有文件，反序列化拿到 install_id
  3. 返回该 install_id
```

错误（非 IOException）→ `HELPER_DPAPI_UNAVAILABLE` 兄弟错误码 `HELPER_INSTALL_ID_UNAVAILABLE`。

**反例（R3.1 之前的不安全流程，已废弃）**：
> "文件不存在 → `Guid.NewGuid()` → File.WriteAllText" —— 两个进程同时通过 "文件不存在" 检查时各自生成不同 GUID，后写的覆盖先写的；两个进程内存中的 installId 不一致 → Mutex 名不同 → 单例失效。

**隔离模型（R3 范围明确化）**：

| 资源 | 作用域 | 实现 |
|---|---|---|
| `install-id.json` | per-user-per-machine（跨 session 共享） | `%APPDATA%\YuantusPLM\install-id.json` |
| Mutex `Local\YuantusCadHelper-{installId}` | **per-user-per-session**（Windows `Local\` 命名空间天然 session-scoped） | 同一用户在 console session 与 RDP session 内会各自启动一个 helper 实例 —— 这是 R3 显式接受的语义 |
| `helper-session-{sessionId}.json` | per-session（文件名内嵌 sessionId） | `%APPDATA%\YuantusPLM\helper-session-{sessionId}.json` |
| 端口 | per-session（每个 session 独立扫 7959-7999） | session 之间端口可重叠也可不同 —— 由发现文件区分 |
| `config.json` / `audit.db` / `logs` | per-user（跨 session 共享） | 路径不变 |

**R3 不支持跨 RDP session 共享同一 helper 进程**。如有此需求需要切换到 `Global\` Mutex 命名空间（要求 `SeCreateGlobalPrivilege` 或服务化部署），超出本稿范围 —— 见 §8 未决问题。

**Shared 端发现逻辑**：用 `Process.GetCurrentProcess().SessionId` 取当前 session，组装 `helper-session-{sessionId}.json` 路径。LISP 桥 / `CADDedupPlugin` / Tauri Companion 都通过 Shared 走，不需要各自重复实现。

### 5.2 端口分配与发现

**端口**：从 `7959` 起线性尝试到 `7999`，绑定 `127.0.0.1` 失败则下一个。**绝不**绑定 `0.0.0.0`。

**发现文件**：`%APPDATA%\YuantusPLM\helper-session-{sessionId}.json`（R3.2：原 `helper.json` 改名，文件名内嵌 sessionId 以实现 per-session 隔离）

```json
{
  "schema_version": "1.0",
  "session_id": 2,
  "port": 7959,
  "pid": 12345,
  "image_path": "C:\\Users\\frank\\AppData\\Roaming\\YuantusPLM\\helper\\yuantus-cad-helper.exe",
  "started_at": "2026-05-19T11:30:00+08:00",
  "protocol_version": "1.0",
  "helper_version": "0.1.0",
  "endpoints_base": "http://127.0.0.1:7959"
}
```

R3.2 在 schema 中新增 `session_id` 与 `image_path`：前者用于异常情况下校验发现文件归属（防多 session 间误读），后者用于 §5.1 步骤 5 的 PID + 映像路径双匹配。

退出时（含 SIGTERM / 命令面板 quit）**必须**删除当前 session 对应的发现文件。崩溃残留由 §5.1 的 5/6 步兜底。

### 5.3 鉴权模型 + Local Token Bootstrap / Reset

**两层 token**：

| 层 | 名称 | 作用 | 存储 |
|---|---|---|---|
| 1 | local-helper-token | 浏览器/跨站误触防护（防 `fetch('http://127.0.0.1:7959/...')` 这类被动攻击）；非主防线 | DPAPI 用户作用域 + 固定 entropy `"yuantus-cad-helper-local-token-v1"` |
| 2 | plm-user-token | helper ↔ Yuantus PLM 服务端 | DPAPI 用户作用域 + 固定 entropy `"yuantus-cad-plm-bearer-v1"` |

> **关于 DPAPI 安全语义**：DPAPI 仅提供"用户作用域静态加密落盘"保护，**不阻止**同一 Windows 用户下其他进程通过同一 API 读取。R3 把 `X-Yuantus-Local-Token` 定位为**浏览器/跨站误触防护**（任何浏览器内 JS 拿不到该 token，因此 `fetch` 到 helper 直接 401）。本机调用边界的**主防线**是来源 PID + 进程映像路径白名单。进程映像签名校验列入后续增强。

#### 5.3.1 Local Token Bootstrap 流程（R3 新增）

**首次启动（DPAPI 中无 token）**：

```
Shared spawn helper.exe（不带任何 token 头）
   │
   ▼
helper 进程启动：
  a. 拿命名 Mutex（§5.1）
  b. 检查 DPAPI `(scope=CurrentUser, entropy="yuantus-cad-helper-local-token-v1")`：
     - 若已有 token → 读出，内存常驻
     - 若无 token → 生成 32 字节加密随机数（`RandomNumberGenerator.GetBytes(32)`），
                    hex 编码为字符串，写入 DPAPI；写失败 → 启动失败，
                    退出码 HELPER_LOCAL_TOKEN_BOOTSTRAP_FAILED
  c. 启动 Kestrel，写当前 session 的 helper-session-{sessionId}.json
  d. /healthz 开始返回 200
   │
   ▼
Shared 等 /healthz 200（最多 5 秒）
   │
   ▼
Shared 在发第一次受保护请求前：
  从同一 DPAPI scope + entropy 读取 token
  注入 X-Yuantus-Local-Token: <hex>
  发请求
   │
   ▼
helper 校验：内存中常驻 token 与请求头比对，匹配 → 通过
```

**关键不变量**：
- token 永不通过 HTTP 传递；只通过 DPAPI 落盘 + 读取
- helper 是**生成者**，Shared 是**消费者**；二者用相同 DPAPI scope + entropy
- helper 必须在 `/healthz` 开始返回 200 之前完成 token 写入；否则 Shared 读到的可能是空
- 同用户 DPAPI 跨进程可读，无需任何 HTTP/handshake 传递 token

**Token 失效自修复**：

- Shared 发请求遇 `401 AUTH_LOCAL_TOKEN_INVALID` → 重读 DPAPI 一次
- 仍失败 → 给用户提示："请在终端运行 `yuantus-cad-helper.exe --reset-local-token` 重新生成本地配对密钥"

#### 5.3.2 `--reset-local-token` 命令（R3 新增）

**仅限本机交互式执行**。**helper 不提供 HTTP 端点用于 reset；不接受任何远程触发**。

```
yuantus-cad-helper.exe --reset-local-token
```

执行流程：
1. **必须**在交互式终端运行（检测 `Console.IsInputRedirected == false` 且 `Environment.UserInteractive == true`）；从 IPC / 子进程 / 远程 shell 触发时直接拒绝并退出码 `1`
2. 提示用户："此操作将作废当前本地配对密钥，所有 CAD 内运行中的会话需要重新调用 helper 才能继续。是否继续？[y/N]"
3. 用户确认 `y`/`Y` 才继续
4. 检测 Mutex `Local\YuantusCadHelper-{installId}`：
   - 已被占（另一个 helper 在跑）→ 提示用户先关闭 / 等空闲超时；不强制杀进程
   - 未被占 → 继续
5. 写新 32 字节随机 token 到 DPAPI（覆盖旧值）
6. 输出新 token 的 hex 长度（不输出 token 本身）+ "下次 CAD 调用时会自动重新拉取新密钥"
7. 退出码 `0`

**安全约束（关键）**：
- 该命令**绝不**通过 HTTP / Named Pipe / 子进程参数等任何 IPC 形式暴露
- helper 服务模式下**没有** `/admin/reset-token` 或类似端点
- 工程上 grep `--reset-local-token` 应只命中 `Program.cs` 的命令行解析路径，不应在 HTTP route handler 中出现

**`/diff/preview` / `/sync/inbound` 等业务端点鉴权**：

- 每次 HTTP 请求必须带 `X-Yuantus-Local-Token: <hex>`
- helper 启动时从 DPAPI 读出明文，常驻内存；不在 HTTP 响应 / 日志中回显
- `Yuantus.Cad.Shared` 在发请求前从 DPAPI 读出同一 token 注入头

**第 2 层校验（PLM token）**：

- helper 内部把第 2 层 token 注入对 PLM 的请求 `Authorization: Bearer ...`
- 同时注入 `x-tenant-id` / `x-org-id` 头（与 `MaterialSyncApiClient.ConfigureHeaders` 一致）
- 不暴露给客户端

**来源加固（主防线，默认开）**：

- helper 检查请求 socket 的对端 PID（`GetExtendedTcpTable`）
- 仅允许进程映像名 + 路径双匹配：
  - `acad.exe` 匹配 `C:\Program Files\Autodesk\AutoCAD*\acad.exe`
  - `ZWCAD.exe` 匹配 `C:\Program Files\ZWSOFT\ZWCAD*\ZWCAD.exe`
  - `gscad.exe` 匹配 `C:\Program Files\Gstarsoft\GstarCAD*\gscad.exe`
  - `yuantus-tauri-companion.exe` 匹配 `%APPDATA%\YuantusPLM\companion\*`
- 失败时返回 `403 ORIGIN_PROCESS_NOT_ALLOWED`
- 白名单可在 `%APPDATA%\YuantusPLM\config.json` 扩展
- 进程映像签名校验留后续增强

### 5.4 HTTP 端点（R3 范围）

约定：
- 所有请求 `Content-Type: application/json; charset=utf-8`
- 所有响应统一信封：`{ "ok": bool, "data"?: T, "error"?: { code, message, retryable, details } }`
- 业务异常返回 `200 OK` + `ok=false`；HTTP 层错误（鉴权 / 来源）才用 4xx 状态码
- 协议版本头：客户端发 `X-Yuantus-Protocol: 1.0`；helper 在不兼容时返回 `426 Upgrade Required`
- **helper 端点路径直接对齐服务端原路径**（省一层翻译；运维一眼对得上）

| Method | helper path | 鉴权 | 来源 | 透传到服务端 | 对应 .NET 客户端方法 | R3 必备 |
|---|---|---|---|---|---|---|
| GET | `/healthz` | ❌ 豁免 | ❌ 豁免 | — | — | ✅ |
| GET | `/version` | ❌ 豁免 | ❌ 豁免 | — | — | ✅ |
| POST | `/session/login` | ✅ 本地 token | ✅ | `/auth/login`（注入 tenant_id / org_id） | — | ✅ |
| POST | `/session/logout` | ✅ | ✅ | — | — | ✅ |
| GET | `/session/status` | ✅ | ✅ | — | — | ✅ |
| POST | `/cad/current-drawing` | ✅ | ✅ | — | — | ✅ |
| POST | `/diff/preview` | ✅ | ✅ | `/plugins/cad-material-sync/diff/preview` | `DiffPreviewAsync` | ✅ |
| POST | `/sync/inbound` | ✅ | ✅ | `/plugins/cad-material-sync/sync/inbound` | `SyncInboundAsync`（**PLMMATPUSH 用此**） | ✅ |
| POST | `/sync/outbound` | ✅ | ✅ | `/plugins/cad-material-sync/sync/outbound` | `SyncOutboundAsync` | ✅ |
| POST | `/audit/apply-result` | ✅ | ✅ | （本地审计） | — | ✅ |
| POST | `/dedup/check` | ✅ | ✅ | `/api/dedup/check`（**multipart/form-data** 转发，非 JSON） | `DedupApiClient.CheckDuplicateAsync` | ✅ |
| POST | `/shell/notify` | ✅ | ✅ | — | — | ✅ |
| POST | `/compose` | ✅ | ✅ | `/plugins/cad-material-sync/compose` | `ComposeAsync` | ⏳ R3+ |
| POST | `/validate` | ✅ | ✅ | `/plugins/cad-material-sync/validate` | `ValidateAsync` | ⏳ R3+ |
| GET | `/tasks/{id}` | ✅ | ✅ | — | — | ⏳ R3+ |
| POST | `/diagnostics/snapshot` | ✅ | ✅ | — | — | ⏳ R3+ |

**`/diff/preview` 入参收敛**：

- 服务端 `CadDiffPreviewRequest` 中 `item_id` 是**可选**，且支持 `values` / `target_properties` / `target_cad_fields` 等其他路径。
- **R3 helper 仅暴露 item_id 路径**：helper 校验 `item_id` 非空，否则返回 `400` + `HELPER_INPUT_VALIDATION_FAILED`。
- 其他服务端能力（values / target_properties / target_cad_fields）R3 helper 不暴露；如有需要在 R3+ 评估。

**`/sync/inbound` 入参 / 响应**（**保证 PLMMATPUSH 命令不退化**）：

请求（透传服务端 `SyncInboundRequest`，由 helper 注入 tenant/org/Bearer）：
```json
{
  "drawing": {
    "filename": "J2824002-06.dwg",
    "filepath": "D:\\projects\\demo\\"
  },
  "profile_id": "sheet",
  "cad_fields": {
    "图号": "J2824002-06",
    "材料": "Q345",
    "长": 1200,
    "宽": 600,
    "厚": 5
  },
  "dry_run": false,
  "overwrite": false,
  "create_if_missing": false,
  "cad_system": "autocad"
}
```

响应（透传服务端 `SyncInboundResponse`）：
```json
{
  "ok": true,
  "data": {
    "ok": true,
    "action": "updated",
    "profile_id": "sheet",
    "item_id": "ITEM-01HQX...",
    "properties": { "material": "Q345", "length": 1200, "width": 600, "thickness": 5 },
    "updates": { "material": { "before": "Q235", "after": "Q345" } },
    "cad_fields": { "图号": "J2824002-06", "材料": "Q345", "长": 1200, "宽": 600, "厚": 5 },
    "conflicts": [],
    "matched_items": [],
    "errors": [],
    "warnings": []
  }
}
```

**`/diff/preview` 入参 / 响应示例**：

请求：
```json
{
  "drawing": {
    "filename": "J2824002-06.dwg",
    "filepath": "D:\\projects\\demo\\",
    "is_titled": true,
    "modified": false
  },
  "profile_id": "sheet",
  "item_id": "ITEM-01HQX...",
  "current_cad_fields": {
    "图号": "J2824002-06",
    "材料": "Q235",
    "厚": null
  },
  "include_empty": false,
  "cad_system": "autocad"
}
```

响应（透传服务端 `CadDiffPreviewResponse`，外加 helper 侧 `pull_id`）：
```json
{
  "ok": true,
  "data": {
    "pull_id": "PULL-01HQY...",
    "server_response": {
      "ok": true,
      "profile_id": "sheet",
      "item_id": "ITEM-01HQX...",
      "properties": { "material": "Q345", "thickness": 5 },
      "current_cad_fields": { "图号": "J2824002-06", "材料": "Q235" },
      "target_cad_fields": { "材料": "Q345", "厚": 5, "图号": "J2824002-06" },
      "write_cad_fields": { "材料": "Q345", "厚": 5 },
      "requires_confirmation": true,
      "diffs": [
        { "name": "material", "current": "Q235", "incoming": "Q345" },
        { "name": "thickness", "current": null, "incoming": 5 }
      ],
      "summary": { "changed": 2, "unchanged": 1 },
      "warnings": []
    }
  }
}
```

**`/audit/apply-result` 入参 / 响应**：

请求：
```json
{
  "pull_id": "PULL-01HQY...",
  "applied_fields": { "材料": "Q345", "厚": 5 },
  "failed_fields": [],
  "outcome": "ok",
  "drawing": {
    "filename": "J2824002-06.dwg",
    "filepath": "D:\\projects\\demo\\"
  },
  "cad_system": "autocad",
  "duration_ms": 312
}
```

响应：
```json
{ "ok": true, "data": { "audit_id": "AUD-01HQZ..." } }
```

`outcome` 枚举：`ok` / `partial` / `failed` / `not-applied-display-only`（国产 CAD R3 路线常用）。

`pull_id` 校验：helper 在 `/diff/preview` 时缓存 `{pull_id, drawing_path, write_cad_fields, ts}`（TTL 10 分钟）。`/audit/apply-result` 传入未知 `pull_id` → `404 AUDIT_PULL_ID_UNKNOWN`；同一 `pull_id` 二次上报 → `409 AUDIT_ALREADY_REPORTED`（审计幂等）。

### 5.5 错误码体系

| 前缀 | 范围 |
|---|---|
| `HELPER_*` | helper 自身故障（端口、DPAPI、单例、bootstrap） |
| `AUTH_*` | 第 1/第 2 层鉴权失败 |
| `ORIGIN_*` | 来源校验失败 |
| `CAD_*` | DWG 读写适配层错误（**仅 CADDedupPlugin 内部抛出后透传**） |
| `AUDIT_*` | 审计上报错误 |
| `PLM_*` | 转发 PLM 服务端错误（透传 PLM `error.code`） |
| `PROTO_*` | 协议版本不兼容 |
| `HELPER_INPUT_*` | helper 层入参校验失败（例如 `/diff/preview` 缺 item_id） |

**保留错误码（R3 最小集）**：
- `HELPER_PORT_BUSY` / `HELPER_DPAPI_UNAVAILABLE` / `HELPER_SINGLETON_LOST` / **`HELPER_UNHEALTHY`（R3.2 新增；helper 进程存活但 /healthz 不通）** / **`HELPER_LOCAL_TOKEN_BOOTSTRAP_FAILED`（R3 新增）** / **`HELPER_INSTALL_ID_UNAVAILABLE`（R3.2 新增；install-id.json 既无法 CreateNew 也无法读回）**
- `AUTH_LOCAL_TOKEN_MISSING` / `AUTH_LOCAL_TOKEN_INVALID` / `AUTH_PLM_NOT_LOGGED_IN` / `AUTH_TENANT_MISSING`
- `ORIGIN_PROCESS_NOT_ALLOWED`
- `CAD_DRAWING_NOT_TITLED` / `CAD_DRAWING_MODIFIED` / `CAD_FIELD_WRITE_FAILED`
- `AUDIT_PULL_ID_UNKNOWN` / `AUDIT_ALREADY_REPORTED` / `AUDIT_PULL_ID_EXPIRED`
- `PLM_ITEM_NOT_FOUND` / `PLM_PROFILE_MISMATCH` / `PLM_VALIDATION_FAILED` / `PLM_INBOUND_CONFLICT`
- `PROTO_VERSION_UNSUPPORTED`
- `HELPER_INPUT_VALIDATION_FAILED`（R3 新增；用于 helper 层入参收敛检查）

每个错误码必须有：
- 一句话给最终用户看的中文 message
- 一条 `retryable` 标记
- 一段日志侧 `details`（不入 HTTP 响应，仅落本地日志）

### 5.6 日志 + 审计

**日志**（`Serilog` + 文件 sink）：
- 路径：`%APPDATA%\YuantusPLM\logs\helper-YYYY-MM-DD.log`
- 默认保留 14 天，按日轮转
- 不写 token 明文；token 字段统一 mask 为 `***<last4>`
- 级别：`Information` 起，`Debug` 由 `--verbose` 或 `config.json` 切换

**审计**（SQLite `%APPDATA%\YuantusPLM\audit.db`）：

```sql
CREATE TABLE audit_events (
  id INTEGER PRIMARY KEY,
  ts TEXT NOT NULL,              -- ISO 8601 with tz
  endpoint TEXT NOT NULL,        -- /diff/preview | /sync/inbound | /sync/outbound | /audit/apply-result | /dedup/check
  drawing_path TEXT,
  profile_id TEXT,
  item_id TEXT,
  pull_id TEXT,                  -- 关联 /diff/preview 与 /audit/apply-result
  cad_system TEXT,               -- autocad | zwcad | gstarcad
  outcome TEXT NOT NULL,         -- ok | partial | failed | not-applied-display-only | error
  error_code TEXT,
  duration_ms INTEGER NOT NULL,
  trace_id TEXT NOT NULL,        -- 透传到 PLM 服务端
  applied_fields_json TEXT,
  failed_fields_json TEXT
);

CREATE INDEX idx_audit_ts ON audit_events(ts);
CREATE INDEX idx_audit_pull ON audit_events(pull_id);
```

仅记 `/diff/preview` / `/sync/inbound` / `/sync/outbound` / `/audit/apply-result` / `/dedup/check` / `/session/login` / `/session/logout` 这种"对图纸或 PLM 数据/会话有副作用"的端点；`/healthz` / `/version` 不记。

`--reset-local-token` 执行也写一条特殊审计（endpoint=`internal:reset-local-token`，outcome=`ok`/`error`）。

### 5.7 LISP 适配层（R3 协议规范）

> 这一节定义"如何从 LISP 调到 helper"，但**不实现具体 LISP 文件** —— 那是后续 Step 3 的范围。R3 在这里冻结接口契约。
>
> **关键**：本节描述**国产 CAD 路线**。AutoCAD 路线（`CADDedupPlugin`）是 C# 进程内直接 `ProjectReference` `Yuantus.Cad.Shared (net46 target)`，**不**走 LISP 桥。

**问题**：AutoCAD/ZWCAD/GstarCAD 的 LISP `startapp` 是 fire-and-forget，无法读响应。

**方案**：随 helper bundle 一并部署 `YuantusCadHelperBridge.dll`（基于 **.NET Framework v4.6**，`ProjectReference` `Yuantus.Cad.Shared` 的 net46 target），通过 `NETLOAD` 在 CAD 进程内加载，导出一条 LISP 函数：

```
(yuantus-helper-call "<endpoint>" "<json-request-string>")
  → returns "<json-response-string>" 或 nil（异常时把错误打到命令行）
```

其内部职责（**只做 transport，不做业务**）：
1. 通过 `Yuantus.Cad.Shared` 读 `%APPDATA%\YuantusPLM\helper-session-{sessionId}.json` 取端口（sessionId 取自当前进程）
2. 若 helper 进程未起：spawn `yuantus-cad-helper.exe`，轮询 `/healthz`（5s 超时）
3. 从 DPAPI 读 `local-helper-token`，注入 `X-Yuantus-Local-Token`
4. 同步调用 helper 指定 endpoint，将响应 JSON 字符串返回给 LISP
5. 网络 / DPAPI 异常：返回 nil 并 `(princ "...")` 写错误码到 CAD 命令行

**关键设计点**：
- LISP 永不直接发 HTTP、永不直接读 DPAPI、永不持有 token
- `YuantusCadHelperBridge.dll` **不**做业务逻辑（不写 DWG、不解析 diff），只是 transport
- DWG 字段写入由各 CAD 自己的 .NET 适配负责。AutoCAD = 现有 `CadMaterialFieldService.cs`；ZWCAD/GstarCAD R3 阶段**不做**

### 5.8 安全考量

| 风险 | 缓解 |
|---|---|
| 浏览器从 `<img>`/`fetch` 攻击 helper | 1) 仅绑 loopback；2) 不允许 CORS；3) 必带 `X-Yuantus-Local-Token`（浏览器内 JS 拿不到 DPAPI 数据）；4) **主防线**：来源 PID + 路径白名单 |
| 同机其他用户读 token | DPAPI 用户作用域加密；跨用户隔离由 Windows 用户机制保证 |
| **同机同用户其他进程读 token** | **R3 不防**。此场景下任意进程都能调 DPAPI。来源 PID + 路径白名单是边界；进程签名校验列入后续增强 |
| token 在日志中泄露 | logger 中间件硬编码字段 mask；CI 加 grep 测试 |
| 端口冲突被恶意进程预占 | 第 1 层 token + 来源 PID/路径校验；客户端检测连接但 401 → 视为冲突，提示用户人工排查 |
| helper 被诱导转发到非 Yuantus 服务端 | helper 内硬编码 `server_allowlist`（出厂只允许 `*.yuantus.tld` + 用户在 Companion 配置过的内网地址） |
| LISP 注入 / JSON 大小炸弹 | 单请求 16 MB 上限；JSON 解析 depth ≤ 64；正则白名单校验文件路径 |
| 假冒进程映像名（`acad.exe` 仿冒） | 路径双匹配（必须在 `C:\Program Files\Autodesk\AutoCAD*\` 下）；进程映像签名校验留后续增强 |
| **远程触发 token 重置** | `--reset-local-token` 强制交互式终端检测；helper 服务模式无任何 HTTP 端点可重置 token；CI grep `--reset-local-token` 不应在 HTTP route handler 中出现 |

**R3 不做**：本机用户互相隔离（多用户共用一台 Windows 时，第二个用户用自己的 DPAPI 密钥重装一份 helper 即可）；进程映像签名校验；server_allowlist 的运行时下发更新。

### 5.9 配置文件

`%APPDATA%\YuantusPLM\config.json`：

```json
{
  "schema_version": "1.0",
  "server_url": "https://plm.example.com",
  "tenant_id": "tenant-acme",
  "org_id": "org-engineering",
  "default_profile_id": "sheet",
  "idle_timeout_minutes": 30,
  "log_level": "Information",
  "origin_whitelist": [
    { "image_name": "acad.exe",
      "path_pattern": "C:\\Program Files\\Autodesk\\AutoCAD*\\acad.exe" },
    { "image_name": "ZWCAD.exe",
      "path_pattern": "C:\\Program Files\\ZWSOFT\\ZWCAD*\\ZWCAD.exe" },
    { "image_name": "gscad.exe",
      "path_pattern": "C:\\Program Files\\Gstarsoft\\GstarCAD*\\gscad.exe" },
    { "image_name": "yuantus-tauri-companion.exe",
      "path_pattern": "*\\YuantusPLM\\companion\\yuantus-tauri-companion.exe" }
  ],
  "server_allowlist": [
    "https://plm.example.com",
    "https://*.yuantus.internal"
  ]
}
```

由 Tauri Companion（后续 Step）生成与维护；R3 阶段也可手工创建。

`tenant_id` / `org_id` / `default_profile_id` 通过 `/session/login` 首次写入；helper 重启后从 `config.json` 重读。

### 5.10 验收测试（Step 2）

**CI 跑（GitHub Actions Windows runner，无 AutoCAD）**

| # | 测试 |
|---|---|
| 1 | 端口 `7959` 被占用 → 自动走 `7960`，`helper-session-{sessionId}.json` 写正确端口 |
| 2 | DPAPI 不可用 → 启动失败，错误码 `HELPER_DPAPI_UNAVAILABLE` |
| 3 | **DPAPI 写失败（mock）→ 启动失败，错误码 `HELPER_LOCAL_TOKEN_BOOTSTRAP_FAILED`** |
| 4 | 单例 Mutex 已存在 + helper-session 文件存在 + **裸 GET /healthz**（无 token） 200 → 退出 0 |
| 4b | **R3.2**：单例 Mutex 被占 + /healthz 失败 + 文件中 PID 进程仍存活且映像匹配 → `HELPER_UNHEALTHY`（**不**删除 helper-session 文件，**不**重试拿 Mutex） |
| 4c | **R3.2**：单例 Mutex 被占 + /healthz 失败 + 文件中 PID 进程不存在 / 映像不匹配 → 删除 helper-session 文件 + 重试 Mutex 成功 |
| 4d | **R3.2**：探活路径**不依赖**本地 token（grep 单例恢复代码：probe 调用不应注入 X-Yuantus-Local-Token） |
| 4e | **R3.2**：`install-id.json` 原子生成 —— mock 两个进程同时尝试 `FileMode.CreateNew`，只有一个成功；另一个 IOException 后重读拿到同一 install_id |
| 4f | **R3.2**：两个 session 的 helper 同时启动 —— 各自写自己的 `helper-session-{sessionId}.json`，互不覆盖；两个 helper 同时跑（Local\ Mutex 是 session-scoped）|
| 5 | 错误信封：业务异常返回 `200 OK` + `ok=false`，HTTP 层错误用 4xx |
| 6 | `/healthz` `/version` 豁免鉴权 + 豁免来源 → 裸 curl 应 200 |
| 7 | `/session/status` 缺 token → `401 AUTH_LOCAL_TOKEN_MISSING` |
| 8 | `/session/status` 错误 token → `401 AUTH_LOCAL_TOKEN_INVALID` |
| 9 | `/session/status` 缺 `tenant_id`（首次未登录） → 返回 `logged_in=false` 而非错误 |
| 10 | **`/diff/preview` 缺 `item_id` → `400` + `HELPER_INPUT_VALIDATION_FAILED`（helper 层强制，不透传到服务端）** |
| 11 | **`/sync/inbound` 透传成功 → 服务端返回 `action="updated"`，helper 落审计** |
| 12 | **`/sync/inbound` 服务端返回 conflict → helper 透传 `PLM_INBOUND_CONFLICT` 错误码** |
| 13 | `/sync/outbound` 透传成功 → 返回服务端 cad_fields |
| 14 | `/audit/apply-result` 未知 `pull_id` → `404 AUDIT_PULL_ID_UNKNOWN` |
| 15 | `/audit/apply-result` 重复上报 → `409 AUDIT_ALREADY_REPORTED` |
| 16 | 端口绑定 `0.0.0.0` 的代码路径不存在（源码 grep） |
| 17 | 日志 grep `token=` 仅出现 mask 后的 `***xxxx` |
| 18 | server_allowlist 之外的 URL → helper 直接拒绝转发 |
| 19 | **首次启动 bootstrap：helper 进程启动时若 DPAPI 无 token → 自动生成并写入；`/healthz` 200 之前 token 必须已落 DPAPI** |
| 20 | **`--reset-local-token` 在非交互式环境（`Console.IsInputRedirected == true`）→ 拒绝并退出码 1** |
| 21 | **helper 服务模式 HTTP route table grep `reset-local-token` → 必须为空** |
| 22 | 与 PLM 的契约用 mock：login 携带 tenant_id；diff/preview 入参/响应字段完整匹配服务端 schema；sync/inbound 入参字段完整匹配 |

**真机手测（人工验收证据，存档录像/截图）**

| # | 测试 |
|---|---|
| 1 | Windows 11 + AutoCAD 2018 真机：`Yuantus.Cad.Shared (net46)` 集成进 `CADDedupPlugin`，运行 `PLMMATPULL` → helper 自动 spawn → `/diff/preview` 返回 `write_cad_fields` → `CadMaterialFieldService` 写 DWG → `/audit/apply-result` 落 audit.db |
| 2 | **AutoCAD 2018 真机：运行 `PLMMATPUSH` → helper `/sync/inbound` 透传服务端 → 服务端返回 updated action → 审计落盘** |
| 3 | Procmon 录像证明 detector 零注册表写（.pml 存档） |
| 4 | LAN 另一台机访问 `http://<host-lan-ip>:7959/healthz` → 拒绝（loopback only） |
| 5 | 模拟非白名单进程（用 `curl.exe` 直接发请求） → `403 ORIGIN_PROCESS_NOT_ALLOWED` |
| 6 | helper 进程 `taskkill` → `helper-session-{sessionId}.json` 残留 → 下次启动按 §5.1 R3.2 流程第 5/6 步删除并正常工作 |
| 7 | 空闲 30 分钟自动退出，当前 session 的 `helper-session-{sessionId}.json` 清理 |
| 8 | helper 与现有 `CADDedupPlugin` 共存运行 30 分钟无端口/Mutex 冲突，无内存泄漏 |
| 9 | ZWCAD 真机：装 LISP 瘦壳 + `YuantusCadHelperBridge.dll`（.NET Framework v4.6），运行 `YUANTUS_DIFF_PREVIEW` → 命令行显示 `write_cad_fields` JSON，不自动写 DWG；`/audit/apply-result` 落 `not-applied-display-only` |
| 10 | **`--reset-local-token` 在 PowerShell 真机执行 → 提示确认 → 用户输入 y → DPAPI 中 token 被替换 → 既有 AutoCAD 会话下次 `PLMMATPULL` 自动重新拿新 token 成功** |
| 11 | **`--reset-local-token` 从 SSH / WinRM 远程触发 → 拒绝并退出码 1** |
| 12 | **AutoCAD 2018 真机：`Yuantus.Cad.Shared` 同时被 net46 加载（在 acad.exe 内）与 net6.0 加载（在 helper.exe 内），无运行时冲突** |

---

## 6. 端到端调用序列

### 6.1 AutoCAD 拉物料（PLMMATPULL，强插件路径）

```
User                CADDedupPlugin (in acad.exe)   helper.exe              PLM API
 │                       │                            │                      │
 │  PLMMATPULL           │                            │                      │
 ├──────────────────────▶│                            │                      │
 │                       │ Yuantus.Cad.Shared (net46) │                      │
 │                       │   读 helper-session-       │                      │
 │                       │     {sessionId}.json       │                      │
 │                       │   helper 不在 → spawn      │                      │
 │                       ├───────────────────────────▶│                      │
 │                       │   helper 启动 →            │                      │
 │                       │   bootstrap DPAPI token    │                      │
 │                       │   /healthz 200             │                      │
 │                       │◀── 200 ────────────────────│                      │
 │                       │                            │                      │
 │                       │   Shared 读 DPAPI 取 token │                      │
 │                       │ POST /diff/preview         │                      │
 │                       │   { item_id, profile_id,   │                      │
 │                       │     current_cad_fields }   │                      │
 │                       │   + X-Yuantus-Local-Token  │                      │
 │                       ├───────────────────────────▶│                      │
 │                       │                            │ POST /plugins/cad-   │
 │                       │                            │   material-sync/     │
 │                       │                            │   diff/preview       │
 │                       │                            │ (Bearer + x-tenant-  │
 │                       │                            │  id + x-org-id)      │
 │                       │                            ├─────────────────────▶│
 │                       │                            │◀── CadDiffPreviewResp│
 │                       │                            │   含 write_cad_fields│
 │                       │   pull_id + 服务端响应      │                      │
 │                       │◀───────────────────────────│                      │
 │                       │                            │                      │
 │  MaterialSyncDiff     │                            │                      │
 │  PreviewWindow 显示   │                            │                      │
 │  diffs                │                            │                      │
 │◀──────────────────────│                            │                      │
 │  点击确认              │                            │                      │
 ├──────────────────────▶│                            │                      │
 │                       │ CadMaterialFieldService    │                      │
 │                       │   .ApplyWrite(             │                      │
 │                       │     write_cad_fields)      │                      │
 │                       │ ↓ (acad.exe 进程内，直接   │                      │
 │                       │    操作 Document/BlockRef) │                      │
 │                       │                            │                      │
 │                       │ POST /audit/apply-result   │                      │
 │                       │   { pull_id,               │                      │
 │                       │     applied_fields,        │                      │
 │                       │     outcome: "ok",         │                      │
 │                       │     duration_ms }          │                      │
 │                       ├───────────────────────────▶│                      │
 │                       │                            │ SQLite insert audit  │
 │                       │◀── { audit_id } ───────────│                      │
 │  Toast + 命令行提示    │                            │                      │
 │◀──────────────────────│                            │                      │
```

### 6.2 ZWCAD/GstarCAD 拉物料（瘦 LISP 路径，R3 不写 DWG）

```
User              LISP (in ZWCAD/GstarCAD)    YuantusCadHelperBridge.dll    helper.exe          PLM API
 │                    │                              │                          │                  │
 │  YUANTUS_DIFF_     │                              │                          │                  │
 │  PREVIEW           │                              │                          │                  │
 ├───────────────────▶│                              │                          │                  │
 │                    │ (yuantus-helper-call         │                          │                  │
 │                    │   "/diff/preview" payload)   │                          │                  │
 │                    ├─────────────────────────────▶│                          │                  │
 │                    │                              │ Shared (net46)           │                  │
 │                    │                              │   discovery + spawn      │                  │
 │                    │                              │   + token 注入           │                  │
 │                    │                              ├─────────────────────────▶│                  │
 │                    │                              │                          │ /plugins/...     │
 │                    │                              │                          ├─────────────────▶│
 │                    │                              │                          │◀── 含 write_     │
 │                    │                              │                          │   cad_fields ────│
 │                    │                              │◀── 服务端响应 + pull_id ─│                  │
 │                    │◀── JSON string ──────────────│                          │                  │
 │                    │                              │                          │                  │
 │                    │ (princ "建议写入：...")      │                          │                  │
 │                    │ 命令行展示 write_cad_fields  │                          │                  │
 │                    │ **不自动写 DWG**             │                          │                  │
 │◀───────────────────│                              │                          │                  │
 │                    │                              │                          │                  │
 │                    │ (yuantus-helper-call         │                          │                  │
 │                    │   "/audit/apply-result"      │                          │                  │
 │                    │   { pull_id, outcome:        │                          │                  │
 │                    │     "not-applied-display-only"})│                       │                  │
 │                    ├─────────────────────────────▶│                          │                  │
 │                    │                              ├─────────────────────────▶│                  │
 │                    │                              │                          │ SQLite insert    │
 │                    │                              │◀── { audit_id } ─────────│                  │
 │                    │◀── ok ────────────────────────│                          │                  │
```

### 6.3 AutoCAD 推物料（PLMMATPUSH，强插件路径，R3 新增）

```
User                CADDedupPlugin (in acad.exe)   helper.exe              PLM API
 │                       │                            │                      │
 │  PLMMATPUSH           │                            │                      │
 ├──────────────────────▶│                            │                      │
 │                       │ CadMaterialFieldService    │                      │
 │                       │   .ReadCurrentFields()     │                      │
 │                       │ ↓ (acad.exe 进程内读取    │                      │
 │                       │    DWG 标题栏 / 块属性)   │                      │
 │                       │                            │                      │
 │                       │ Yuantus.Cad.Shared (net46) │                      │
 │                       │   ensure helper running    │                      │
 │                       │ POST /sync/inbound         │                      │
 │                       │   { profile_id,            │                      │
 │                       │     cad_fields,            │                      │
 │                       │     dry_run: false,        │                      │
 │                       │     overwrite,             │                      │
 │                       │     create_if_missing,     │                      │
 │                       │     cad_system: autocad }  │                      │
 │                       ├───────────────────────────▶│                      │
 │                       │                            │ POST /plugins/cad-   │
 │                       │                            │   material-sync/     │
 │                       │                            │   sync/inbound       │
 │                       │                            ├─────────────────────▶│
 │                       │                            │◀── SyncInboundResp ──│
 │                       │                            │   { action, item_id, │
 │                       │                            │     updates,         │
 │                       │                            │     conflicts? }     │
 │                       │   透传 + 审计落盘           │                      │
 │                       │◀───────────────────────────│                      │
 │                       │                            │                      │
 │  显示结果（updated /  │                            │                      │
 │  created / conflict） │                            │                      │
 │◀──────────────────────│                            │                      │
```

**关键变更点**（R3 vs R2）：
- R2 错误地删除了 `/material/push` 端点；R3 恢复 `/sync/inbound`（与服务端 + 既有客户端命名 1:1 对齐）
- PLMMATPUSH 命令在 `CADDedupPlugin.cs:535` 已实存；R3 helper 必须支持其透传，否则命令直接失效

---

## 7. 与现有 Yuantus 代码的集成点

| 现有 / 新增 | 路径 | R3 改动 |
|---|---|---|
| `clients/autocad-material-sync/CADDedupPlugin/MaterialSyncApiClient.cs` | 既有 | **保留**，但内部 HTTP 调用迁移到 `Yuantus.Cad.Shared (net46 target)`；保留同一公开 API 形状（`DiffPreviewAsync` / `SyncInboundAsync` / `SyncOutboundAsync`）作为业务层入口 |
| `clients/autocad-material-sync/CADDedupPlugin/DedupApiClient.cs` | 既有 | **保留公开 API 形状**（`CheckDuplicateAsync` 等），但内部 HTTP 调用迁移到 `Yuantus.Cad.Shared (net46 target)` → helper `/dedup/check`；保证"helper 是唯一 PLM 出口"成立。**注意**：`/api/dedup/check` 是 multipart/form-data 文件上传，迁移时 Shared 需支持 multipart 转发，不是 JSON 透传 |
| `clients/autocad-material-sync/CADDedupPlugin/CadMaterialFieldService.cs` | 既有 | **零改动**。DWG 读写仍由 acad.exe 进程内调用 |
| `clients/autocad-material-sync/CADDedupPlugin/MaterialSyncDiffPreviewWindow.xaml(.cs)` | 既有 | **零改动** |
| `clients/autocad-material-sync/CADDedupPlugin/DedupPlugin.cs`（PLMMATPULL / PLMMATPUSH 命令） | 既有 | **零改动**对外形为；内部走 `MaterialSyncApiClient` → Shared → helper 链路 |
| `clients/autocad-material-sync/CADDedupPlugin/UserIdentification.cs` | 既有 | **保留**；helper 内部新增同等概念的 `LocalIdentity`（在 Shared 中） |
| `plugins/yuantus-cad-material-sync/main.py` | 既有 | **零改动**。helper 只是新增客户端 |
| `services/cad-extractor/` `services/cad-connector/` | 既有 | **零改动**；helper R3 不代理这两个服务 |
| `clients/cad-desktop-helper/Shared/` | **新增**：`Yuantus.Cad.Shared.csproj`（**`<TargetFrameworks>net46;net6.0</TargetFrameworks>`**） | helper discovery + DPAPI token + HTTP transport client + 错误信封 + 注册表抽象 + bootstrap 流程 |
| `clients/cad-desktop-helper/Detector/` | **新增**：`yuantus-cad-detector.csproj`（**net6.0 self-contained**） | 引用 Shared net6.0；只读注册表 + 文件系统扫描 |
| `clients/cad-desktop-helper/Helper/` | **新增**：`yuantus-cad-helper.csproj`（**net6.0 self-contained**） | 引用 Shared net6.0；Kestrel loopback；端点实现；SQLite 审计；`--reset-local-token` 子命令 |
| `clients/cad-desktop-helper/Bridge/` | **新增**：`YuantusCadHelperBridge.csproj`（**`<TargetFrameworkVersion>v4.6</TargetFrameworkVersion>`**） | 引用 Shared net46；NETLOAD 暴露 `(yuantus-helper-call ...)` LISP 函数 |
| `clients/cad-desktop-helper/Solution/YuantusCadDesktop.sln` | **新增** | 把上述四个 + 既有 `CADDedupPlugin.csproj` 一并组织 |

**关键引用关系（R3 修订）**：

```
Yuantus.Cad.Shared (multi-target: net46;net6.0)
   │
   ├─◀─ CADDedupPlugin (.NET Framework v4.6/v4.8 multi-config)
   │        │
   │        └─ ProjectReference Shared → 自动选 net46 target
   │
   ├─◀─ YuantusCadHelperBridge (.NET Framework v4.6)
   │        │
   │        └─ ProjectReference Shared → 自动选 net46 target
   │
   ├─◀─ yuantus-cad-helper (.NET 6 self-contained)
   │        │
   │        └─ ProjectReference Shared → 自动选 net6.0 target
   │
   └─◀─ yuantus-cad-detector (.NET 6 self-contained)
            │
            └─ ProjectReference Shared → 自动选 net6.0 target
```

**为什么多目标而非 netstandard2.0**：
- AutoCAD 2018 基线 `CADDedupPlugin.csproj:16` 是 `TargetFrameworkVersion = v4.6`
- netstandard2.0 官方矩阵要求 .NET Framework v4.6.1+；v4.6 在矩阵外
- multi-target `net46;net6.0` 让同一份源码出两套程序集，分别给 .NET Framework 客户端和 .NET 6 服务端使用，零兼容性风险
- NETLOAD 进 acad.exe 必须为完整 .NET Framework 程序集；netstandard2.0 类库即使被 .NET Framework 引用，也会引入 facade 依赖问题

---

## 8. 未决问题（R3 状态）

| # | 问题 | R3 决议 |
|---|---|---|
| 1 | helper 的 `server_url` / `tenant_id` 来源 | 用户首次 `/session/login` 时传入，写 `config.json`；不出厂硬编码 |
| 2 | detector / helper 是否合并为同一 exe + 子命令 | 分两个 exe（detector 一次性、helper 常驻；语义不同；CI/OOBE 单独跑 detector 启动开销低） |
| 3 | R3 是否给 ZWCAD/GstarCAD 写 DWG | **不写**。仅做探测 + LISP 瘦壳 + helper 调用 + diff 展示。写回适配放后续阶段，需独立真机验证 |
| 4 | 进程白名单是否需要签名校验 | R3 用进程名 + 路径双匹配；签名校验列入后续增强 |
| 5 | 本地审计 SQLite 是否上行到 PLM | R3 仅本地落盘；上行设计另起独立 doc |
| 6 | `/compose` / `/validate` / `/tasks/{id}` 是否在 R3 范围 | 不在；R3+ 评估 |
| 7 | helper 是否暴露服务端 `/diff/preview` 的 values/target_properties 等其他路径 | **不暴露**；R3 仅 item_id 路径 |
| 8 | **跨 RDP session 是否共享同一 helper 进程**（R3.2 新增） | **R3 不支持**。隔离模型为 per-user-per-session（见 §5.1.1）；跨 session 共享需要切换到 `Global\` Mutex（要求 `SeCreateGlobalPrivilege` 或服务化部署），超出本稿范围。如有真实需求，独立稿评估 |

---

## 9. 验收门槛（R3 合并条件）

| # | 门槛 | CI / 手测 |
|---|---|---|
| 1 | §5.10 全部 CI 用例通过；GitHub Actions Windows runner 跑 | CI |
| 2 | §5.10 全部真机用例完成，提供录像 / Procmon .pml / 审计 SQLite 截图作为证据 | 手测 |
| 3 | detector：Windows 11 + AutoCAD 2018 真机输出正确 JSON | 手测 |
| 4 | helper 与现有 `CADDedupPlugin` 共存 30 分钟无端口/Mutex 冲突、无内存泄漏 | 手测 |
| 5 | `Yuantus.Cad.Shared` net46 引入 `CADDedupPlugin` 后，原 `PLMMATPULL` / `PLMMATPUSH` 命令行为不退化 | 手测 + CI 单测 |
| 6 | **PLMMATPUSH 真机回归通过：CADDedupPlugin → helper `/sync/inbound` → 服务端 `/plugins/cad-material-sync/sync/inbound` → 物料正确入库** | 手测 |
| 7 | **Local token bootstrap 真机回归：全新机器首次安装 helper，第一次 `PLMMATPULL` 不卡住，DPAPI 中 token 正确生成** | 手测 |
| 8 | **`--reset-local-token` 真机回归：交互式终端可执行；SSH/WinRM 远程触发被拒** | 手测 |
| 9 | **Shared 多目标编译验证：`dotnet build` 产出 `Yuantus.Cad.Shared.dll` 同时包含 `lib/net46/` 与 `lib/net6.0/` 两个目标** | CI |
| 10 | ZWCAD 真机：装 LISP 瘦壳 + Bridge.dll（v4.6），运行 `YUANTUS_DIFF_PREVIEW` 拿到 `write_cad_fields` 并展示 | 手测 |
| 11 | 文档：本设计稿 + 一份运维 README + 一份 `verify_helper_e2e.ps1` 脚本 | 文档 |
| 12 | Codex / 独立评审通过；任何与本稿冲突的实现差异必须在 PR 描述里显式列出 | 评审 |

---

## 10. 工作分解（R3 估算）

| Slice | 内容 | 估时 |
|---|---|---|
| S1 | `Yuantus.Cad.Shared`：多目标 `net46;net6.0` 工程结构；helper discovery + DPAPI 封装（bootstrap + reset） + HTTP transport + 错误信封 + 注册表抽象 | **2 天**（R2 1.5 天 + 多目标 / bootstrap 复杂度） |
| S2 | detector：注册表 + 文件系统扫描 + JSON schema + Procmon 零写验证模板 | 1.5 天 |
| S3 | helper：Kestrel loopback + 端口分配 + `helper-session-{sessionId}.json` 生命周期 + install-id.json 原子生成 + 单例恢复完整算法（R3.2 PID + 路径双匹配、HELPER_UNHEALTHY 分支、裸 /healthz 探活）+ bootstrap token 生成 | **1.5 天**（R3.2 增加原子生成 + 死活判定复杂度） |
| S4 | helper：DPAPI token 第 1/2 层鉴权 + 来源 PID + 路径白名单 | 1 天 |
| S5 | helper：`/healthz` `/version` `/session/*`（含 tenant_id / org_id / default_profile_id） + `/cad/current-drawing` | 1 天 |
| S6 | helper：`/diff/preview` + `/sync/inbound` + `/sync/outbound` + `pull_id` 缓存 + `/audit/apply-result` + SQLite | **2 天**（R2 1.5 天 + 增加两个透传端点） |
| S7 | helper：`--reset-local-token` 交互式命令 + 拒绝非交互 + 审计落盘 | 0.5 天 |
| S8 | `CADDedupPlugin` 重构：内部 `MaterialSyncApiClient` **和** `DedupApiClient` 的 HTTP 调用都改走 `Yuantus.Cad.Shared (net46)` → helper（其中 dedup 走 multipart 转发）；PLMMATPULL apply 后新增 `/audit/apply-result` 上报；公开 API 形状不变 | **2 天**（增加 DedupApiClient 迁移 + multipart 支持） |
| S9 | `YuantusCadHelperBridge.dll`（.NET Framework v4.6）：NETLOAD 适配 + `(yuantus-helper-call ...)` LISP 函数 | 1 天 |
| S10 | ZWCAD/GstarCAD LISP 瘦壳：`YUANTUS_DIFF_PREVIEW` 等命令 + 命令行展示（不写 DWG） | 1 天 |
| S11 | 集成 + 验收测试 + 文档 | 2 天 |

总计：约 **15.5 个工作日**，单人节奏；可两人并行到 9 个工作日。每个 Slice 走独立 PR + 单独 opt-in（按 memory 中"每个 phase 独立 opt-in"规则）。

**Slice 顺序依赖**：S1 是基础，必须先做；S2/S3 可并行；S4 依赖 S1；S5/S6 依赖 S3/S4；S7 依赖 S4；S8 依赖 S1+S6；S9 依赖 S1；S10 依赖 S9；S11 收尾。

---

## 11. 变更记录

| 版本 | 日期 | 变更 |
|---|---|---|
| R1 | 2026-05-19 | 初稿。覆盖 detector + helper（已作废） |
| R2 | 2026-05-19 | Codex 第一轮审阅修订。3 High（DWG 责任 / tenant / item_id）+ 4 Medium（DPAPI 措辞 / healthz 验证矛盾 / 单例残留 / CI vs 手测）；新增三层引用架构（Shared + CADDedupPlugin + Bridge）。**R3 替代 R2，R2 已作废**（已作废） |
| R3 | 2026-05-19 | Codex 第二轮审阅修订。**High**：.NET 目标框架与 AutoCAD 2018 v4.6 基线对齐 —— Shared 改多目标 `net46;net6.0` 替代 netstandard2.0；Bridge.dll 改 `.NET Framework v4.6`；helper/detector 保持 net6.0 self-contained。**High**：恢复 `/sync/inbound` 与 `/sync/outbound` 端点（透传服务端同名路径），保证 `PLMMATPUSH` 命令不退化。新增 §6.3 PLMMATPUSH 时序图。**Medium**：`item_id` 强制约束改为 helper 层范围限定，服务端 `CadDiffPreviewRequest.item_id` 实为可选；helper 不暴露 values/target_properties 等其他路径。**Medium**：新增 §5.3.1 Local Token Bootstrap 流程（helper 启动时生成 + 写 DPAPI + Shared 读 DPAPI 注入）；新增 §5.3.2 `--reset-local-token` 命令（强制交互式终端检测、拒绝远程触发、helper 服务模式不暴露任何 HTTP reset 端点）。新增错误码 `HELPER_LOCAL_TOKEN_BOOTSTRAP_FAILED` / `HELPER_INPUT_VALIDATION_FAILED`。验收新增 CI 用例 19/20/21、手测用例 10/11/12 与门槛 6/7/8/9。工作分解从 13 天升到 14.5 天。 |
| R3.1 | 2026-05-19 | Codex 第三轮审阅修订（commit 前最后一轮收敛）。**Medium**：补 `DedupApiClient.cs` 的迁移说明 —— §7 集成点表新增该客户端的内部 HTTP 调用走 `Yuantus.Cad.Shared` → helper `/dedup/check`，且 `/api/dedup/check` 是 **multipart/form-data** 文件上传（非 JSON），Shared 需支持 multipart 转发；§5.4 端点表对应行补上 multipart 标注与对应 .NET 客户端方法；§10 S8 合并 MaterialSyncApiClient + DedupApiClient 两客户端迁移并加 multipart 实现，估时 1.5 → 2 天；总工作量 14.5 → 15 天。此修订保证"helper 是唯一与 PLM 服务端通信的本机出口"在范围与代码两侧都成立。 |
| R3.2 | 2026-05-19 | Codex 第四轮审阅修订（PR #614 reviewer comment 响应）。**High**：`install-id.json` 改为原子生成 —— 用 `FileStream(FileMode.CreateNew)` 独占创建；IOException 回退立即重读既有文件；废弃 "exists 检查 + WriteAllText" 的竞态写法。新增 §5.1.1 子节专门描述。**Medium**：明确 per-user-per-session 隔离模型 —— `Local\YuantusCadHelper-{installId}` Mutex 天然 session-scoped；`helper.json` 改名 `helper-session-{sessionId}.json`，文件名内嵌 sessionId；schema 新增 `session_id` + `image_path` 字段；跨 RDP session 共享同一 helper 进程**不支持**，列入未决问题 #8。**Medium**：单例恢复探活改为**裸 GET /healthz**（不带本地 token），把"helper 是否活着"与"DPAPI 是否可读"解耦；持有 Mutex 但 /healthz 失败时按 PID + 映像路径双校验区分 `HELPER_UNHEALTHY`（存活但不健康，不删发现文件）与 `HELPER_SINGLETON_LOST`（持有者已死，删文件重试）。新增错误码 `HELPER_UNHEALTHY` / `HELPER_INSTALL_ID_UNAVAILABLE`。验收新增 CI 用例 4b/4c/4d/4e/4f。§10 S3 估时 1 → 1.5 天，总工时 15 → 15.5 天。 |
