# Yuantus AutoCAD Material Sync Client

## 📋 简介

这是 Yuantus 仓库内维护的 AutoCAD 客户端源码，来源于既有 `CADDedup` 插件能力，并在其上接入 `yuantus-cad-material-sync` 服务端插件。它同时保留图纸查重命令，并新增 PLM 物料属性、规格合成、CAD 明细栏/标题栏读写和差异确认回填能力。

### 核心功能

- **PLM 物料同步**：读取/回填 AutoCAD 标题栏块属性和明细表字段
- **规格合成**：按 Yuantus profile 将板材、管材、棒材、锻件等字段合成为规格
- **差异确认**：`PLMMATPULL` 先展示本地差异窗口，确认后只写回变化字段
- **自动查重**：保存图纸时自动检查重复，无需手动操作
- **即时通知**：通过命令行和Windows通知立即显示检查结果
- **详细对比**：发现重复时可查看并排对比
- **灵活配置**：可自定义相似度阈值、通知方式等

## 🚀 快速开始

### 系统要求

- **操作系统**：Windows 10/11 64位
- **AutoCAD版本**：AutoCAD 2018（最低兼容基线）
- **.NET Framework**：4.6 或更高；AutoCAD 2018 构建默认使用 4.6
- **网络要求**：能访问 Yuantus PLM API 服务

### 构建和安装

1. **在 Windows + AutoCAD 环境进入客户端目录**
   ```batch
   cd clients\autocad-material-sync
   ```

2. **构建 AutoCAD 2018 版本**
   ```batch
   build_simple.bat
   ```

3. **运行安装程序**
   ```batch
   install.bat
   ```

4. **重启AutoCAD**
   - 关闭所有AutoCAD实例
   - 重新启动AutoCAD

5. **验证安装**
   ```
   # 在AutoCAD命令行输入
   DEDUPHELP
   ```
   如果显示帮助信息，说明安装成功

### 首次配置

1. **配置服务器地址**
   ```
   # AutoCAD命令行
   DEDUPCONFIG
   ```

2. **在配置对话框中设置**：
   - 服务器地址：`http://Yuantus服务器IP:7910`
   - 租户 ID、组织 ID、物料 profile
   - 点击"测试连接"确认连接成功
   - 点击"确定"保存

3. **开始使用**
   - 正常在AutoCAD中绘图
   - 保存图纸（Ctrl+S）
   - 查看命令行和桌面通知获取查重结果

## 📖 使用指南

### 自动查重

**最常用的方式** - 无需任何操作，自动工作：

1. 在AutoCAD中完成绘图
2. 按 `Ctrl+S` 保存图纸
3. 查看命令行消息：
   ```
   🔍 CAD查重: 正在检查图纸...

   ✓ 未发现重复，图纸已添加到查重库
   ```
   或者：
   ```
   ⚠️  发现重复图纸！
      相似度: 92.5%
      相似图纸: 支架-V2.dwg
      输入 DEDUPVIEW 查看详细对比
   ```

4. 同时会弹出Windows桌面通知，点击可查看详细对比

### 手动查重

如果想手动检查当前图纸：

```
# AutoCAD命令行
DEDUPCHECK
```

### 查看详细对比

发现重复后，查看详细对比：

```
# AutoCAD命令行
DEDUPVIEW
```

会自动打开浏览器显示并排对比界面。

### 可用命令

| 命令 | 功能 | 使用场景 |
|-----|------|---------|
| `DEDUPCHECK` | 手动检查当前图纸 | 保存前想提前检查 |
| `DEDUPVIEW` | 查看上次检查结果 | 再次查看对比详情 |
| `DEDUPCONFIG` | 打开配置对话框 | 修改设置 |
| `DEDUPHELP` | 显示帮助信息 | 查看命令列表 |
| `DEDUPSTATS` | 显示使用统计 | 查看节省了多少工时 |
| `PLMMATPROFILES` | 查看 PLM 物料 profile | 确认板材、管材、棒材、锻件字段 |
| `PLMMATCOMPOSE` | 合成物料规格并回填 CAD | 用户输入长宽厚等字段后写回明细栏 |
| `PLMMATPUSH` | 从 CAD 提取字段并同步到 PLM | 从标题栏/明细表回写物料字段 |
| `PLMMATPULL` | 从 PLM 拉取字段并回填 CAD | 按 Item ID 先差异预览，确认后写回图纸 |
| `PLMMATASSIST` | 物料助手：解析候选并经确认创建 Draft | resolve 只展示精确/相似/草稿建议；取消零写入；确认后创建 Draft；不回写 DWG |

物料同步功能详见 `PLM_MATERIAL_SYNC_GUIDE.md`。

`PLMMATPULL` 会调用 Yuantus `/diff/preview`，在本地差异预览窗口中展示 CAD 当前值和 PLM 目标值。用户确认后，插件只写回服务端返回的 `write_cad_fields`，不会把未变化字段重新写入 DWG。

macOS 本地可执行物料同步静态和 fixture 验证：

```
python3 clients/autocad-material-sync/verify_material_sync_static.py
python3 clients/autocad-material-sync/verify_material_sync_fixture.py
python3 clients/autocad-material-sync/verify_material_sync_e2e.py
python3 clients/autocad-material-sync/verify_material_sync_db_e2e.py
```

Windows 构建默认面向 AutoCAD 2018。如果需要高版本构建，可通过环境变量切换：

```batch
REM AutoCAD 2018 baseline
build_simple.bat

REM AutoCAD 2024 explicit build
set AUTOCAD_VERSION=2024
set AUTOCAD_INSTALL_DIR=C:\Program Files\Autodesk\AutoCAD 2024
build_simple.bat
```

Windows 实机验收请按 `WINDOWS_AUTOCAD2018_VALIDATION_GUIDE.md` 执行。正式编译和 DWG smoke 前可先运行：

```powershell
powershell -ExecutionPolicy Bypass -File .\verify_autocad2018_preflight.ps1
```

## ⚙️ 配置说明

### 配置对话框

输入 `DEDUPCONFIG` 打开配置对话框，包含4个选项卡：

#### 1. 服务器设置

- **服务器地址**：Yuantus PLM API 地址（必填；查重功能也复用该服务地址）
  - 格式：`http://IP地址:端口`
  - 示例：`http://192.168.1.100:7910`
- **API密钥**：如果服务器启用了认证（可选）
- **超时时间**：请求超时时间（秒），默认30秒
- **测试连接**：点击测试服务器是否可访问

#### 2. 行为设置

- **自动检查**：保存时自动检查（推荐启用）
- **相似度阈值**：判定为重复的相似度（默认85%）
  - 90%以上：严格模式，仅检测几乎相同的图纸
  - 80-90%：标准模式（推荐）
  - 80%以下：宽松模式，可能产生误报
- **自动添加到索引**：新图纸自动加入查重库（推荐启用）
- **高相似度提示**：相似度>90%时提示查看对比

#### 3. 通知设置

- **未发现重复时显示通知**：不推荐，会产生干扰
- **发现重复时播放声音**：推荐启用，引起注意

#### 4. 用户信息

- **用户名**：用于统计（自动填充当前Windows用户名）
- **部门**：用于统计（可选）

### 配置文件位置

配置文件自动保存在：
```
C:\Users\你的用户名\AppData\Roaming\CADDedup\config.json
```

可以手动编辑该文件，但建议通过配置对话框修改。

## 💡 使用技巧

### 1. 理解相似度

相似度表示两张图纸的相似程度：

- **95-100%**：几乎完全相同，可能只是标题栏、尺寸标注不同
- **85-95%**：高度相似，核心几何图形相同，可能有局部修改
- **70-85%**：部分相似，有共同的设计元素
- **<70%**：不太相似

### 2. 发现重复后的处理

**如果相似度>90%**：
1. 点击通知或输入 `DEDUPVIEW` 查看详细对比
2. 确认是否可以直接使用已有图纸
3. 如果可以，关闭当前图纸，打开已有图纸使用
4. 如果需要修改，基于已有图纸修改，避免从头设计

**如果相似度80-90%**：
1. 查看对比，评估重复程度
2. 考虑是否可以复用已有图纸的部分设计
3. 如果完全不同的项目，可以继续使用新图纸

### 3. 提高查重准确性

- 使用标准化的绘图规范
- 统一图纸模板和图框
- 及时清理临时图纸和测试文件
- 定期审查查重结果，调整相似度阈值

### 4. 查看统计数据

定期输入 `DEDUPSTATS` 查看：
- 总检查次数
- 发现重复次数
- 重复率
- 预估节省工时

示例输出：
```
========================================
使用统计
========================================
总检查次数: 156
发现重复: 23
唯一图纸: 133
重复率: 14.7%
预估节省工时: 46.0 小时
========================================
```

## 🔧 故障排除

### 问题1：插件未加载

**症状**：输入命令提示"未知命令"

**解决方法**：
1. 检查插件目录是否存在：
   ```
   %APPDATA%\Autodesk\ApplicationPlugins\CADDedup.bundle
   ```
2. 确认AutoCAD版本是否支持（2018 为最低兼容基线）
3. 重新运行 `install.bat`
4. 完全关闭AutoCAD后重启

### 问题2：无法连接服务器

**症状**：命令行显示"无法连接到查重服务器"

**解决方法**：
1. 输入 `DEDUPCONFIG` 检查服务器地址是否正确
2. 点击"测试连接"按钮
3. 确认网络连接正常
4. 联系IT部门确认服务器是否运行
5. 检查防火墙设置

### 问题3：检查速度慢

**症状**：保存后等待时间长

**解决方法**：
1. 检查网络连接速度
2. 增加超时时间（`DEDUPCONFIG` → 超时时间）
3. 文件过大（>10MB）时会较慢，属正常现象
4. 联系IT部门检查服务器性能

### 问题4：通知不弹出

**症状**：命令行有消息，但没有Windows通知

**解决方法**：
1. 检查Windows通知设置：
   - 设置 → 系统 → 通知 → 确保通知已开启
2. 检查AutoCAD进程是否有通知权限
3. 重启AutoCAD

### 问题5：误报（不相似的图纸被标记为重复）

**解决方法**：
1. 调高相似度阈值：`DEDUPCONFIG` → 行为设置 → 相似度阈值 → 调至90%以上
2. 查看详细对比，了解为什么系统认为相似
3. 向IT部门反馈，帮助改进算法

## 🔄 更新和卸载

### 更新插件

1. 下载新版本插件包
2. 运行 `install.bat`（会自动覆盖旧版本）
3. 重启AutoCAD

### 卸载插件

1. 运行 `uninstall.bat`
2. 选择是否删除配置文件和统计数据
3. 重启AutoCAD

## 📞 技术支持

### 常见问题

1. **Q: 插件会影响AutoCAD速度吗？**
   - A: 不会。检查在后台异步进行，不会阻塞AutoCAD操作。

2. **Q: 临时图纸会被检查吗？**
   - A: 不会。自动保存、备份文件等临时文件会被忽略。

3. **Q: 可以禁用自动检查吗？**
   - A: 可以。`DEDUPCONFIG` → 行为设置 → 取消勾选"自动检查"。

4. **Q: 数据安全吗？**
   - A: 是的。图纸只上传到公司内部服务器，不会外传。

5. **Q: 发现重复后必须停止使用吗？**
   - A: 不是。系统只是提示，最终决定由您做出。

### 联系方式

- **IT部门**：内线 8888
- **邮箱**：it-support@company.com
- **企业微信**：IT技术支持群

### 反馈建议

如果您有任何建议或发现bug，请通过以下方式反馈：
- 企业微信群：CAD查重系统用户群
- 邮件：it-support@company.com

---

**版本**：v1.0.0
**更新日期**：2024-01-01
**开发团队**：IT部
