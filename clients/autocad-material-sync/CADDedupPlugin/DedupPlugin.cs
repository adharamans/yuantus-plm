// DedupPlugin.cs - AutoCAD图纸查重插件核心类
// 版本: 1.0.0
// 描述: 自动检测CAD图纸保存操作并进行查重检查

using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Threading.Tasks;
using Autodesk.AutoCAD.Runtime;
using Autodesk.AutoCAD.ApplicationServices;
using Autodesk.AutoCAD.DatabaseServices;
using Autodesk.AutoCAD.EditorInput;
using Autodesk.Windows;
using Yuantus.Cad.Shared.Transport;

[assembly: ExtensionApplication(typeof(CADDedupPlugin.DedupPlugin))]
[assembly: CommandClass(typeof(CADDedupPlugin.DedupPlugin))]

namespace CADDedupPlugin
{
    /// <summary>
    /// CAD查重插件主类
    /// </summary>
    public class DedupPlugin : IExtensionApplication
    {
        private static DedupConfig _config;
        private static DedupApiClient _apiClient;
        private static MaterialSyncApiClient _materialSyncClient;
        private static ICadMaterialFieldAdapter<Document> _materialFieldService;
        private static NotificationManager _notificationManager;

        #region 插件生命周期

        /// <summary>
        /// 插件初始化 - AutoCAD启动时调用
        /// </summary>
        public void Initialize()
        {
            try
            {
                // 初始化配置
                _config = DedupConfig.Load();
                _apiClient = new DedupApiClient(_config);
                _materialSyncClient = new MaterialSyncApiClient(_config);
                _materialFieldService = new CadMaterialFieldService();
                _notificationManager = new NotificationManager();

                // 注册文档事件
                RegisterDocumentEvents();

                // 显示启动消息
                ShowWelcomeMessage();

                // 添加Ribbon按钮
                AddRibbonButton();
            }
            catch (System.Exception ex)
            {
                System.Diagnostics.Debug.WriteLine($"CADDedup插件初始化失败: {ex.Message}");
            }
        }

        /// <summary>
        /// 插件卸载 - AutoCAD关闭时调用
        /// </summary>
        public void Terminate()
        {
            UnregisterDocumentEvents();
            _notificationManager?.Dispose();
        }

        #endregion

        #region 事件注册

        /// <summary>
        /// 注册文档相关事件
        /// </summary>
        private void RegisterDocumentEvents()
        {
            var docMgr = Application.DocumentManager;

            // 文档创建事件
            docMgr.DocumentCreated += OnDocumentCreated;

            // 为当前已打开的文档注册事件
            foreach (Document doc in docMgr)
            {
                RegisterDatabaseEvents(doc.Database);
            }
        }

        /// <summary>
        /// 注销文档事件
        /// </summary>
        private void UnregisterDocumentEvents()
        {
            var docMgr = Application.DocumentManager;
            docMgr.DocumentCreated -= OnDocumentCreated;

            foreach (Document doc in docMgr)
            {
                UnregisterDatabaseEvents(doc.Database);
            }
        }

        /// <summary>
        /// 文档创建时注册数据库事件
        /// </summary>
        private void OnDocumentCreated(object sender, DocumentCollectionEventArgs e)
        {
            RegisterDatabaseEvents(e.Document.Database);
        }

        /// <summary>
        /// 注册数据库保存事件
        /// </summary>
        private void RegisterDatabaseEvents(Database db)
        {
            db.SaveComplete += OnSaveComplete;
        }

        /// <summary>
        /// 注销数据库保存事件
        /// </summary>
        private void UnregisterDatabaseEvents(Database db)
        {
            db.SaveComplete -= OnSaveComplete;
        }

        #endregion

        #region 核心功能 - 自动查重

        /// <summary>
        /// 保存完成后自动查重（事件处理器入口）
        /// Bug #4 修复: 添加顶层异常处理，防止未捕获异常导致应用崩溃
        /// </summary>
        private async void OnSaveComplete(object sender, DatabaseIOEventArgs e)
        {
            try
            {
                await OnSaveCompleteAsync(sender, e);
            }
            catch (System.Exception ex)
            {
                // 记录错误但不抛出，防止事件处理器崩溃
                System.Diagnostics.Debug.WriteLine($"OnSaveComplete 未捕获异常: {ex}");

                try
                {
                    var doc = Application.DocumentManager.MdiActiveDocument;
                    var ed = doc?.Editor;
                    ed?.WriteMessage($"\n❌ 查重插件错误: {ex.Message}");
                }
                catch
                {
                    // 即使日志输出失败也不抛出
                }
            }
        }

        /// <summary>
        /// 保存完成后自动查重（实际逻辑）
        /// </summary>
        private async Task OnSaveCompleteAsync(object sender, DatabaseIOEventArgs e)
        {
            // 检查是否启用自动查重
            if (!_config.AutoCheckEnabled)
                return;

            // 获取文件路径：优先使用事件参数，否则使用文档名称
            Document doc = Application.DocumentManager.MdiActiveDocument;
            if (doc == null)
                return;

            Editor ed = doc.Editor;

            string filePath = e.FileName;

            // 调试：输出所有可能的路径来源
            ed.WriteMessage($"\n[DEBUG] e.FileName = '{e.FileName}'");
            ed.WriteMessage($"\n[DEBUG] doc.Name = '{doc.Name}'");

            Database db = sender as Database;
            if (db != null)
            {
                ed.WriteMessage($"\n[DEBUG] db.Filename = '{db.Filename}'");
            }

            // 如果事件参数中的文件名为空或相对路径，使用 Database.Filename
            if (string.IsNullOrEmpty(filePath))
            {
                // 尝试从 Database 获取完整路径
                if (db != null && !string.IsNullOrEmpty(db.Filename))
                {
                    filePath = db.Filename;
                }
                else if (!string.IsNullOrEmpty(doc.Name))
                {
                    filePath = doc.Name;
                }
            }

            // 如果路径不是绝对路径，转换为绝对路径
            if (!string.IsNullOrEmpty(filePath) && !Path.IsPathRooted(filePath))
            {
                // 尝试组合当前目录
                try
                {
                    filePath = Path.GetFullPath(filePath);
                }
                catch (System.Exception ex)
                {
                    ed.WriteMessage($"\n[DEBUG] Path.GetFullPath failed: {ex.Message}");
                }
            }

            ed.WriteMessage($"\n[DEBUG] Final filePath = '{filePath}'");

            // 过滤不需要检查的文件
            if (ShouldSkipFile(filePath))
                return;

            // Bug #1 修复: 删除重复的 Editor ed 声明（已在第143行声明）
            // Bug #5 修复: 添加空引用检查
            if (ed == null)
                return;

            try
            {
                // 显示开始检查消息
                ed.WriteMessage("\n🔍 CAD查重: 正在检查图纸...");

                // 调用API检查重复
                var result = await _apiClient.CheckDuplicateAsync(filePath);

                // 显示结果
                ShowCheckResult(result, filePath, ed);
            }
            catch (System.Exception ex)
            {
                ed.WriteMessage($"\n❌ CAD查重失败: {ex.Message}");
                System.Diagnostics.Debug.WriteLine($"查重异常: {ex}");
            }
        }

        /// <summary>
        /// 判断是否应该跳过该文件
        /// </summary>
        private bool ShouldSkipFile(string filePath)
        {
            if (string.IsNullOrEmpty(filePath))
                return true;

            string fileName = Path.GetFileName(filePath);

            // 跳过临时文件
            if (fileName.StartsWith("~") ||
                fileName.StartsWith("$") ||
                fileName.Contains(".bak") ||
                fileName.Contains(".dwl"))
            {
                return true;
            }

            // 跳过AutoCAD自动保存文件
            if (fileName.Contains("_recover") || fileName.Contains("_backup"))
            {
                return true;
            }

            return false;
        }

        /// <summary>
        /// 显示检查结果
        /// </summary>
        private void ShowCheckResult(DedupResult result, string filePath, Editor ed)
        {
            if (result.IsDuplicate && result.Duplicates.Any())
            {
                var topMatch = result.Duplicates.First();
                int totalMatches = result.Duplicates.Count;

                // 命令行消息 - 显示最相似的一张和总数
                ed.WriteMessage("\n⚠️  发现重复图纸！");
                ed.WriteMessage($"\n   最高相似度: {topMatch.Similarity:P1}");
                ed.WriteMessage($"\n   最相似图纸: {topMatch.FileName}");

                // 如果有多张相似图纸，提示总数
                if (totalMatches > 1)
                {
                    ed.WriteMessage($"\n   ⚡ 共发现 {totalMatches} 张相似图纸");
                    ed.WriteMessage("\n   输入 DEDUPVIEW 查看全部对比详情");
                }
                else
                {
                    ed.WriteMessage("\n   输入 DEDUPVIEW 查看详细对比");
                }
                ed.WriteMessage("\n");

                // Windows通知
                _notificationManager.ShowDuplicateNotification(
                    Path.GetFileName(filePath),
                    topMatch,
                    result.CheckId,
                    totalMatches  // 传递总数
                );

                // 如果相似度很高，询问是否打开对比
                if (topMatch.Similarity > 0.90 && _config.PromptOnHighSimilarity)
                {
                    var options = new PromptKeywordOptions("\n是否查看详细对比？");
                    options.Keywords.Add("Yes");
                    options.Keywords.Add("No");
                    options.Keywords.Default = "Yes";

                    var pr = ed.GetKeywords(options);
                    if (pr.Status == PromptStatus.OK && pr.StringResult == "Yes")
                    {
                        // 打开对比窗口
                        OpenComparisonWindow(result);
                    }
                }
            }
            else
            {
                // 未发现重复
                ed.WriteMessage("\n✓ 未发现重复，图纸已添加到查重库\n");

                if (_config.ShowNotificationOnUnique)
                {
                    _notificationManager.ShowUniqueNotification(
                        Path.GetFileName(filePath)
                    );
                }
            }
        }

        #endregion

        #region AutoCAD命令

        /// <summary>
        /// 手动检查当前图纸
        /// </summary>
        [CommandMethod("DEDUPCHECK")]
        public async void CheckCurrentDrawing()
        {
            Document doc = Application.DocumentManager.MdiActiveDocument;
            if (doc == null)
                return;

            Editor ed = doc.Editor;

            if (string.IsNullOrEmpty(doc.Name) || doc.Name == "Drawing.dwg")
            {
                ed.WriteMessage("\n请先保存图纸后再进行查重检查");
                return;
            }

            ed.WriteMessage($"\n🔍 正在检查: {Path.GetFileName(doc.Name)}");

            try
            {
                var result = await _apiClient.CheckDuplicateAsync(doc.Name);
                ShowCheckResult(result, doc.Name, ed);
            }
            catch (System.Exception ex)
            {
                ed.WriteMessage($"\n❌ 查重失败: {ex.Message}");
            }
        }

        /// <summary>
        /// 查看上次检查结果
        /// </summary>
        [CommandMethod("DEDUPVIEW")]
        public void ViewLastResult()
        {
            var lastResult = _apiClient.GetLastResult();

            if (lastResult == null)
            {
                Application.ShowAlertDialog("没有可显示的检查结果");
                return;
            }

            OpenComparisonWindow(lastResult);
        }

        /// <summary>
        /// 打开配置对话框
        /// </summary>
        [CommandMethod("DEDUPCONFIG")]
        public void OpenConfiguration()
        {
            var configForm = new ConfigForm(_config);

            if (Application.ShowModalDialog(configForm) == System.Windows.Forms.DialogResult.OK)
            {
                _config.Save();
                _apiClient.UpdateConfig(_config);
                _materialSyncClient.UpdateConfig(_config);

                var ed = Application.DocumentManager.MdiActiveDocument?.Editor;
                ed?.WriteMessage("\n✓ 配置已保存");
            }
        }

        /// <summary>
        /// 显示帮助信息
        /// </summary>
        [CommandMethod("DEDUPHELP")]
        public void ShowHelp()
        {
            var ed = Application.DocumentManager.MdiActiveDocument?.Editor;
            if (ed == null) return;

            ed.WriteMessage("\n========================================");
            ed.WriteMessage("\nCAD图纸查重插件 v1.0");
            ed.WriteMessage("\n========================================");
            ed.WriteMessage("\n");
            ed.WriteMessage("\n可用命令:");
            ed.WriteMessage("\n  DEDUPCHECK   - 手动检查当前图纸");
            ed.WriteMessage("\n  DEDUPVIEW    - 查看上次检查结果");
            ed.WriteMessage("\n  DEDUPCONFIG  - 打开配置对话框");
            ed.WriteMessage("\n  DEDUPHELP    - 显示此帮助信息");
            ed.WriteMessage("\n  DEDUPSTATS   - 显示使用统计");
            ed.WriteMessage("\n  PLMMATPROFILES - 查看 PLM 物料 profile");
            ed.WriteMessage("\n  PLMMATCOMPOSE  - 输入物料字段并回填 CAD 明细栏/标题栏");
            ed.WriteMessage("\n  PLMMATPUSH     - 从 CAD 提取字段并同步到 PLM");
            ed.WriteMessage("\n  PLMMATPULL     - 按 PLM Item ID 差异预览并确认回填 CAD");
            ed.WriteMessage("\n  PLMMATASSIST   - 物料助手：解析候选物料并经确认创建 Draft");
            ed.WriteMessage("\n");
            ed.WriteMessage("\n自动检查:");
            ed.WriteMessage("\n  保存图纸时自动检查重复");
            ed.WriteMessage($"\n  当前状态: {(_config.AutoCheckEnabled ? "已启用" : "已禁用")}");
            ed.WriteMessage("\n");
            ed.WriteMessage("\n技术支持: IT部 内线8888");
            ed.WriteMessage("\n========================================\n");
        }

        /// <summary>
        /// 显示使用统计
        /// </summary>
        [CommandMethod("DEDUPSTATS")]
        public void ShowStatistics()
        {
            var stats = _apiClient.GetStatistics();
            var ed = Application.DocumentManager.MdiActiveDocument?.Editor;
            if (ed == null) return;

            ed.WriteMessage("\n========================================");
            ed.WriteMessage("\n使用统计");
            ed.WriteMessage("\n========================================");
            ed.WriteMessage($"\n总检查次数: {stats.TotalChecks}");
            ed.WriteMessage($"\n发现重复: {stats.DuplicatesFound}");
            ed.WriteMessage($"\n唯一图纸: {stats.UniqueDrawings}");
            ed.WriteMessage($"\n重复率: {stats.DuplicateRate:P1}");
            ed.WriteMessage($"\n预估节省工时: {stats.EstimatedTimeSaved:F1} 小时");
            ed.WriteMessage("\n========================================\n");
        }

        /// <summary>
        /// 查看 PLM 物料 profile。
        /// </summary>
        [CommandMethod("PLMMATPROFILES")]
        public async void ListMaterialProfiles()
        {
            var ed = Application.DocumentManager.MdiActiveDocument?.Editor;
            if (ed == null) return;

            try
            {
                var response = await _materialSyncClient.GetProfilesAsync();
                ed.WriteMessage("\n========================================");
                ed.WriteMessage("\nPLM 物料 Profile");
                ed.WriteMessage("\n========================================");
                foreach (var profile in response.Profiles)
                {
                    ed.WriteMessage($"\n  {profile.ProfileId} - {profile.Label}");
                    foreach (var field in profile.Fields)
                    {
                        var required = field.Required ? "*" : "";
                        var unit = string.IsNullOrWhiteSpace(field.Unit) ? "" : $" ({field.Unit})";
                        ed.WriteMessage($"\n    {required}{field.Name} / {field.Label}{unit}");
                    }
                }
                ed.WriteMessage("\n========================================\n");
            }
            catch (System.Exception ex)
            {
                ed.WriteMessage($"\n❌ 获取物料 profile 失败: {ex.Message}");
            }
        }

        /// <summary>
        /// 用户输入物料字段，调用 PLM 合成规格并回填 CAD。
        /// </summary>
        [CommandMethod("PLMMATCOMPOSE")]
        public async void ComposeMaterialSpec()
        {
            var doc = Application.DocumentManager.MdiActiveDocument;
            var ed = doc?.Editor;
            if (doc == null || ed == null) return;

            try
            {
                var profileId = PromptProfileId(ed);
                if (string.IsNullOrWhiteSpace(profileId)) return;

                var profileResponse = await _materialSyncClient.GetProfileAsync(profileId);
                var values = PromptValuesFromProfile(ed, profileResponse.Profile);
                if (values == null) return;

                var response = await _materialSyncClient.ComposeAsync(profileId, values);
                PrintMaterialResponse(ed, response);

                if (response.Ok && response.CadFields.Count > 0)
                {
                    var updated = _materialFieldService.ApplyFields(doc, response.CadFields);
                    ed.WriteMessage($"\n✓ CAD 字段回填完成，更新 {updated} 处");
                }
            }
            catch (System.Exception ex)
            {
                ed.WriteMessage($"\n❌ 物料规格合成失败: {ex.Message}");
            }
        }

        /// <summary>
        /// 从当前 CAD 图纸提取字段并同步到 PLM。
        /// </summary>
        [CommandMethod("PLMMATPUSH")]
        public async void PushMaterialFieldsToPlm()
        {
            var doc = Application.DocumentManager.MdiActiveDocument;
            var ed = doc?.Editor;
            if (doc == null || ed == null) return;

            try
            {
                var profileId = PromptProfileId(ed);
                if (string.IsNullOrWhiteSpace(profileId)) return;

                var cadFields = _materialFieldService.ExtractFields(doc);
                if (cadFields.Count == 0)
                {
                    ed.WriteMessage("\n未从当前图纸标题栏/明细表中提取到可同步字段");
                    return;
                }

                var dryRun = PromptBoolean(ed, "是否仅预演，不写入 PLM？", _config.MaterialSyncDryRunDefault);
                var overwrite = false;
                if (!dryRun)
                {
                    overwrite = PromptBoolean(ed, "是否允许覆盖 PLM 已有字段？", false);
                }

                var response = await _materialSyncClient.SyncInboundAsync(
                    profileId,
                    cadFields,
                    dryRun,
                    overwrite,
                    createIfMissing: true);

                PrintMaterialResponse(ed, response);
                if (response.CadFields.Count > 0)
                {
                    var updated = _materialFieldService.ApplyFields(doc, response.CadFields);
                    ed.WriteMessage($"\n✓ CAD 合成字段回填完成，更新 {updated} 处");
                }
            }
            catch (System.Exception ex)
            {
                ed.WriteMessage($"\n❌ CAD 字段同步到 PLM 失败: {ex.Message}");
            }
        }

        /// <summary>
        /// 物料助手：按当前图纸字段解析候选物料（只展示），用户显式确认后创建 Draft 物料。
        /// 保守 v1：resolve 不写库；取消零写入；create 仅在显式确认后调用；不回写 DWG
        /// （assistant/create 不返回 CAD field package，本期不写 DWG 字段或关系）。
        /// </summary>
        [CommandMethod("PLMMATASSIST")]
        public async void RunMaterialAssistant()
        {
            var doc = Application.DocumentManager.MdiActiveDocument;
            var ed = doc?.Editor;
            if (doc == null || ed == null) return;

            try
            {
                var profileId = PromptProfileId(ed);
                if (string.IsNullOrWhiteSpace(profileId)) return;

                var cadFields = _materialFieldService.ExtractFields(doc);
                var resolve = await _materialSyncClient.ResolveAsync(
                    profileId,
                    cadFields,
                    new Dictionary<string, object>());

                ed.WriteMessage("\n===== 物料助手 (PLMMATASSIST) =====");
                ed.WriteMessage($"\n状态: {(resolve.Ok ? "OK" : "失败")}  profile: {resolve.ProfileId}");
                ed.WriteMessage($"\n精确匹配: {resolve.ExactMatches.Count}  相似候选: {resolve.SimilarCandidates.Count}  建议新增: {(resolve.DraftSuggested ? "是" : "否")}");
                foreach (var warning in resolve.Warnings)
                {
                    ed.WriteMessage($"\n  ⚠ {warning}");
                }
                foreach (var issue in resolve.Errors)
                {
                    ed.WriteMessage($"\n  ❌ {issue.Code}: {issue.Message}");
                }
                if (resolve.ExactMatches.Count > 0)
                {
                    ed.WriteMessage("\n-- 已存在物料（仅展示，不绑定/不回写）--");
                    foreach (var match in resolve.ExactMatches)
                    {
                        ed.WriteMessage($"\n  {DescribeCandidate(match)}");
                    }
                }
                if (resolve.SimilarCandidates.Count > 0)
                {
                    ed.WriteMessage("\n-- 相似候选（仅展示）--");
                    foreach (var candidate in resolve.SimilarCandidates)
                    {
                        ed.WriteMessage($"\n  {DescribeCandidate(candidate)}");
                    }
                }

                if (!resolve.Ok)
                {
                    return;
                }

                // 仅在用户显式确认后创建 Draft；默认 No；AllowNone 让按 Enter 落到默认 No
                // （同 PromptBoolean 范式）；取消或关闭则零写入。
                var options = new PromptKeywordOptions("\n创建 Draft 物料？")
                {
                    AllowNone = true
                };
                options.Keywords.Add("Yes");
                options.Keywords.Add("No");
                options.Keywords.Default = "No";
                var decision = ed.GetKeywords(options);
                if (decision.Status != PromptStatus.OK || decision.StringResult != "Yes")
                {
                    ed.WriteMessage("\n已取消：未创建任何 PLM 物料，未修改 DWG。");
                    return;
                }

                var create = await _materialSyncClient.CreateAsync(
                    profileId,
                    resolve.ComposedProperties,
                    cadFields,
                    new Dictionary<string, object>());

                if (!create.Ok)
                {
                    ed.WriteMessage("\n❌ 创建失败:");
                    foreach (var issue in create.Errors)
                    {
                        ed.WriteMessage($"\n  {issue.Code}: {issue.Message}");
                    }
                    return;
                }

                ed.WriteMessage("\n✓ 已创建 Draft 物料（未回写 DWG）:");
                ed.WriteMessage($"\n  item_id: {create.ItemId}");
                ed.WriteMessage($"\n  item_number: {create.ItemNumber}");
                ed.WriteMessage($"\n  state: {create.State}  current_state: {create.CurrentState}");
                if (create.DraftCheck != null && create.DraftCheck.Count > 0)
                {
                    ed.WriteMessage($"\n  draft_check: {string.Join(", ", create.DraftCheck.Select(kv => kv.Key + "=" + kv.Value))}");
                }
            }
            catch (System.Exception ex)
            {
                ed.WriteMessage($"\n❌ 物料助手失败: {ex.Message}");
            }
        }

        private static string DescribeCandidate(Dictionary<string, object> candidate)
        {
            var id = candidate.TryGetValue("id", out var idValue) ? idValue : null;
            var score = candidate.TryGetValue("score", out var scoreValue) ? scoreValue : null;
            var high = candidate.TryGetValue("high_similar", out var highValue) ? highValue : null;
            var props = candidate.TryGetValue("properties", out var propsValue) ? propsValue : null;
            var summary = props == null ? "" : props.ToString();
            var scorePart = score == null ? "" : $" score={score}";
            var highPart = (high is bool b && b) ? " [高相似]" : "";
            return $"id={id}{scorePart}{highPart} {summary}";
        }

        /// <summary>
        /// 按 PLM Item ID 预览差异，用户确认后回填 CAD。
        /// </summary>
        [CommandMethod("PLMMATPULL")]
        public async void PullMaterialFieldsFromPlm()
        {
            var doc = Application.DocumentManager.MdiActiveDocument;
            var ed = doc?.Editor;
            if (doc == null || ed == null) return;

            try
            {
                var profileId = PromptProfileId(ed);
                if (string.IsNullOrWhiteSpace(profileId)) return;

                var itemIdPrompt = new PromptStringOptions("\n请输入 PLM Item ID: ")
                {
                    AllowSpaces = false
                };
                var itemIdResult = ed.GetString(itemIdPrompt);
                if (itemIdResult.Status != PromptStatus.OK || string.IsNullOrWhiteSpace(itemIdResult.StringResult))
                {
                    return;
                }

                var currentCadFields = _materialFieldService.ExtractFields(doc);
                var preview = await _materialSyncClient.DiffPreviewAsync(
                    profileId,
                    itemIdResult.StringResult.Trim(),
                    currentCadFields,
                    includeEmpty: false);

                PrintMaterialDiffPreview(ed, preview);
                if (!preview.Ok)
                {
                    return;
                }
                if (preview.WriteCadFields == null || preview.WriteCadFields.Count == 0)
                {
                    ed.WriteMessage("\n✓ 当前 CAD 字段已与 PLM 一致，无需写回");
                    return;
                }

                var window = new MaterialSyncDiffPreviewWindow(preview);
                var confirmed = window.ShowDialog() == true;
                if (!confirmed)
                {
                    ed.WriteMessage("\n已取消 PLM 字段写回 CAD");
                    return;
                }

                var writeFields = window.ConfirmedWriteFields ?? preview.WriteCadFields;
                if (writeFields.Count > 0)
                {
                    var started = DateTime.UtcNow;
                    try
                    {
                        var updated = _materialFieldService.ApplyFields(doc, writeFields);
                        await ReportApplyResultSafely(
                            ed,
                            preview,
                            "ok",
                            writeFields,
                            null,
                            doc,
                            started);
                        ed.WriteMessage($"\n✓ PLM 字段回填 CAD 完成，更新 {updated} 处");
                    }
                    catch (System.Exception applyEx)
                    {
                        await ReportApplyResultSafely(
                            ed,
                            preview,
                            "failed",
                            null,
                            CreateFailedFields(writeFields, applyEx),
                            doc,
                            started);
                        throw;
                    }
                }
            }
            catch (System.Exception ex)
            {
                ed.WriteMessage($"\n❌ 从 PLM 拉取物料字段失败: {ex.Message}");
            }
        }

        #endregion

        #region 物料同步辅助方法

        private string PromptProfileId(Editor ed)
        {
            var defaultProfile = string.IsNullOrWhiteSpace(_config.MaterialProfileId)
                ? "sheet"
                : _config.MaterialProfileId;
            var prompt = new PromptStringOptions($"\n物料 profile [{defaultProfile}]: ")
            {
                AllowSpaces = false
            };
            var result = ed.GetString(prompt);
            if (result.Status == PromptStatus.Cancel)
            {
                return null;
            }
            if (result.Status != PromptStatus.OK || string.IsNullOrWhiteSpace(result.StringResult))
            {
                return defaultProfile;
            }
            return result.StringResult.Trim();
        }

        private async Task ReportApplyResultSafely(
            Editor ed,
            MaterialDiffPreviewResponse preview,
            string outcome,
            Dictionary<string, object> appliedFields,
            Dictionary<string, object> failedFields,
            Document doc,
            DateTime startedUtc)
        {
            if (preview == null || string.IsNullOrWhiteSpace(preview.PullId))
            {
                ed?.WriteMessage("\n⚠️ 无法上报 PLM 写回结果：缺少 pull_id");
                return;
            }

            try
            {
                await _materialSyncClient.ReportApplyResultAsync(
                    preview.PullId,
                    outcome,
                    appliedFields,
                    failedFields,
                    GetDrawingFilename(doc),
                    GetDrawingFilepath(doc),
                    ElapsedMilliseconds(startedUtc));
            }
            catch (System.Exception auditEx)
            {
                ed?.WriteMessage($"\n⚠️ PLM 写回结果审计上报失败 [{AuditErrorCode(auditEx)}]: {auditEx.Message}");
            }
        }

        private static string AuditErrorCode(System.Exception ex)
        {
            var helperEx = ex as HelperException;
            return helperEx == null ? ex.GetType().Name : helperEx.Code;
        }

        private static Dictionary<string, object> CreateFailedFields(
            Dictionary<string, object> attemptedFields,
            System.Exception ex)
        {
            var failed = attemptedFields == null
                ? new Dictionary<string, object>()
                : new Dictionary<string, object>(attemptedFields);
            failed["_error"] = ex == null ? "unknown" : ex.Message;
            return failed;
        }

        private static string GetDrawingFilename(Document doc)
        {
            var path = GetDrawingFilepath(doc);
            return string.IsNullOrWhiteSpace(path) ? null : Path.GetFileName(path);
        }

        private static string GetDrawingFilepath(Document doc)
        {
            return doc == null ? null : doc.Name;
        }

        private static int ElapsedMilliseconds(DateTime startedUtc)
        {
            return Math.Max(0, (int)(DateTime.UtcNow - startedUtc).TotalMilliseconds);
        }

        private Dictionary<string, object> PromptValuesFromProfile(Editor ed, MaterialProfile profile)
        {
            var values = new Dictionary<string, object>(StringComparer.OrdinalIgnoreCase);
            if (profile == null)
            {
                return values;
            }

            foreach (var field in profile.Fields)
            {
                var defaultText = field.DefaultValue == null ? "" : Convert.ToString(field.DefaultValue);
                var label = string.IsNullOrWhiteSpace(field.Label) ? field.Name : field.Label;
                var required = field.Required ? "*" : "";
                var unit = string.IsNullOrWhiteSpace(field.Unit) ? "" : $" ({field.Unit})";
                var defaultHint = string.IsNullOrWhiteSpace(defaultText) ? "" : $" [{defaultText}]";

                var prompt = new PromptStringOptions($"\n{required}{label} / {field.Name}{unit}{defaultHint}: ")
                {
                    AllowSpaces = true
                };
                var result = ed.GetString(prompt);
                if (result.Status == PromptStatus.Cancel)
                {
                    return null;
                }
                var value = result.Status == PromptStatus.OK ? result.StringResult : "";
                if (string.IsNullOrWhiteSpace(value))
                {
                    value = defaultText;
                }
                if (!string.IsNullOrWhiteSpace(value))
                {
                    values[field.Name] = value.Trim();
                }
            }
            return values;
        }

        private bool PromptBoolean(Editor ed, string message, bool defaultValue)
        {
            var options = new PromptKeywordOptions($"\n{message}")
            {
                AllowNone = true
            };
            options.Keywords.Add("Yes");
            options.Keywords.Add("No");
            options.Keywords.Default = defaultValue ? "Yes" : "No";

            var result = ed.GetKeywords(options);
            if (result.Status != PromptStatus.OK)
            {
                return defaultValue;
            }
            return string.Equals(result.StringResult, "Yes", StringComparison.OrdinalIgnoreCase);
        }

        private void PrintMaterialResponse(Editor ed, MaterialSyncResponse response)
        {
            if (response == null)
            {
                ed.WriteMessage("\nPLM 未返回有效响应");
                return;
            }

            ed.WriteMessage("\n========================================");
            ed.WriteMessage("\nPLM 物料同步结果");
            ed.WriteMessage("\n========================================");
            ed.WriteMessage($"\n状态: {(response.Ok ? "OK" : "失败")}");
            if (!string.IsNullOrWhiteSpace(response.ProfileId))
            {
                ed.WriteMessage($"\nProfile: {response.ProfileId}");
            }
            if (!string.IsNullOrWhiteSpace(response.Action))
            {
                ed.WriteMessage($"\n动作: {response.Action}");
            }
            if (!string.IsNullOrWhiteSpace(response.ItemId))
            {
                ed.WriteMessage($"\nItem ID: {response.ItemId}");
            }
            if (response.DryRun)
            {
                ed.WriteMessage("\n模式: dry-run");
            }
            if (response.Properties != null && response.Properties.TryGetValue("specification", out var spec))
            {
                ed.WriteMessage($"\n规格: {spec}");
            }
            if (response.MatchedItems != null && response.MatchedItems.Count > 0)
            {
                ed.WriteMessage($"\n候选物料: {response.MatchedItems.Count}");
            }
            PrintIssues(ed, "错误", response.Errors);
            PrintIssues(ed, "冲突", response.Conflicts);
            if (response.Warnings != null)
            {
                foreach (var warning in response.Warnings)
                {
                    ed.WriteMessage($"\n警告: {warning}");
                }
            }
            ed.WriteMessage("\n========================================\n");
        }

        private void PrintIssues(Editor ed, string label, List<MaterialSyncIssue> issues)
        {
            if (issues == null || issues.Count == 0)
            {
                return;
            }
            foreach (var issue in issues)
            {
                ed.WriteMessage($"\n{label}: {issue.Field} {issue.Code} {issue.Message}");
            }
        }

        private void PrintMaterialDiffPreview(Editor ed, MaterialDiffPreviewResponse preview)
        {
            if (preview == null)
            {
                ed.WriteMessage("\nPLM 未返回有效差异预览");
                return;
            }

            ed.WriteMessage("\n========================================");
            ed.WriteMessage("\nPLM -> CAD 字段差异预览");
            ed.WriteMessage("\n========================================");
            ed.WriteMessage($"\n状态: {(preview.Ok ? "OK" : "失败")}");
            if (!string.IsNullOrWhiteSpace(preview.ProfileId))
            {
                ed.WriteMessage($"\nProfile: {preview.ProfileId}");
            }
            if (!string.IsNullOrWhiteSpace(preview.ItemId))
            {
                ed.WriteMessage($"\nItem ID: {preview.ItemId}");
            }
            if (preview.Summary != null)
            {
                ed.WriteMessage(
                    $"\n差异: added={GetSummaryCount(preview, "added")}, changed={GetSummaryCount(preview, "changed")}, cleared={GetSummaryCount(preview, "cleared")}, unchanged={GetSummaryCount(preview, "unchanged")}");
            }
            var writeCount = preview.WriteCadFields == null ? 0 : preview.WriteCadFields.Count;
            ed.WriteMessage($"\n待写回字段: {writeCount}");
            PrintIssues(ed, "错误", preview.Errors);
            if (preview.Warnings != null)
            {
                foreach (var warning in preview.Warnings)
                {
                    ed.WriteMessage($"\n警告: {warning}");
                }
            }
            ed.WriteMessage("\n========================================\n");
        }

        private int GetSummaryCount(MaterialDiffPreviewResponse preview, string key)
        {
            if (preview == null || preview.Summary == null || string.IsNullOrWhiteSpace(key))
            {
                return 0;
            }
            return preview.Summary.TryGetValue(key, out var value) ? value : 0;
        }

        #endregion

        #region UI相关

        /// <summary>
        /// 显示欢迎消息
        /// </summary>
        private void ShowWelcomeMessage()
        {
            try
            {
                var ed = Application.DocumentManager.MdiActiveDocument?.Editor;
                if (ed != null)
                {
                    ed.WriteMessage("\n========================================");
                    ed.WriteMessage("\n✓ CAD图纸查重插件已加载");
                    ed.WriteMessage($"\n  服务器: {_config.ServerUrl}");
                    ed.WriteMessage($"\n  自动检查: {(_config.AutoCheckEnabled ? "已启用" : "已禁用")}");
                    ed.WriteMessage("\n  输入 DEDUPHELP 查看帮助");
                    ed.WriteMessage("\n========================================\n");
                }
            }
            catch
            {
                // 忽略错误，可能是在后台加载
            }
        }

        /// <summary>
        /// 添加Ribbon按钮
        /// </summary>
        private void AddRibbonButton()
        {
            try
            {
                var ribbonControl = ComponentManager.Ribbon;
                if (ribbonControl == null)
                    return;

                // 查找或创建"插件"选项卡
                var tab = ribbonControl.Tabs.FirstOrDefault(t => t.Title == "插件");
                if (tab == null)
                {
                    tab = new RibbonTab
                    {
                        Title = "插件",
                        Id = "PLUGIN_TAB"
                    };
                    ribbonControl.Tabs.Add(tab);
                }

                // 创建查重面板
                var panel = new RibbonPanel
                {
                    Source = new RibbonPanelSource { Title = "图纸查重" }
                };

                // 添加检查按钮
                var checkButton = new RibbonButton
                {
                    Text = "检查重复",
                    ShowText = true,
                    CommandParameter = "DEDUPCHECK",
                    CommandHandler = new RibbonCommandHandler()
                };
                panel.Source.Items.Add(checkButton);

                // 添加配置按钮
                var configButton = new RibbonButton
                {
                    Text = "配置",
                    ShowText = true,
                    CommandParameter = "DEDUPCONFIG",
                    CommandHandler = new RibbonCommandHandler()
                };
                panel.Source.Items.Add(configButton);

                // 创建物料同步面板
                var materialPanel = new RibbonPanel
                {
                    Source = new RibbonPanelSource { Title = "物料同步" }
                };

                materialPanel.Source.Items.Add(new RibbonButton
                {
                    Text = "规格合成",
                    ShowText = true,
                    CommandParameter = "PLMMATCOMPOSE",
                    CommandHandler = new RibbonCommandHandler()
                });

                materialPanel.Source.Items.Add(new RibbonButton
                {
                    Text = "回写PLM",
                    ShowText = true,
                    CommandParameter = "PLMMATPUSH",
                    CommandHandler = new RibbonCommandHandler()
                });

                materialPanel.Source.Items.Add(new RibbonButton
                {
                    Text = "拉取字段",
                    ShowText = true,
                    CommandParameter = "PLMMATPULL",
                    CommandHandler = new RibbonCommandHandler()
                });

                materialPanel.Source.Items.Add(new RibbonButton
                {
                    Text = "物料助手",
                    ShowText = true,
                    CommandParameter = "PLMMATASSIST",
                    CommandHandler = new RibbonCommandHandler()
                });

                tab.Panels.Add(panel);
                tab.Panels.Add(materialPanel);
            }
            catch (System.Exception ex)
            {
                System.Diagnostics.Debug.WriteLine($"添加Ribbon按钮失败: {ex.Message}");
            }
        }

        /// <summary>
        /// 打开对比窗口
        /// </summary>
        private void OpenComparisonWindow(DedupResult result)
        {
            try
            {
                // 打开浏览器显示详细对比
                string url = $"{_config.ServerUrl}/results/{result.CheckId}";
                System.Diagnostics.Process.Start(url);
            }
            catch (System.Exception ex)
            {
                Application.ShowAlertDialog($"无法打开对比窗口: {ex.Message}");
            }
        }

        #endregion
    }

    /// <summary>
    /// Ribbon命令处理器
    /// </summary>
    internal class RibbonCommandHandler : System.Windows.Input.ICommand
    {
        public event EventHandler CanExecuteChanged;

        public bool CanExecute(object parameter)
        {
            return true;
        }

        public void Execute(object parameter)
        {
            if (parameter is string command)
            {
                Document doc = Application.DocumentManager.MdiActiveDocument;
                doc?.SendStringToExecute(command + " ", true, false, false);
            }
        }
    }
}
