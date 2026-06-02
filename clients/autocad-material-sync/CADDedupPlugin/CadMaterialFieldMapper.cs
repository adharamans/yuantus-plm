// CadMaterialFieldMapper.cs - CAD 物料字段规范化与表格映射规则
using System;
using System.Collections.Generic;
using System.Globalization;

namespace CADDedupPlugin
{
    /// <summary>
    /// 不依赖具体 CAD SDK 的字段别名、抽取和回填规则。
    /// </summary>
    public class CadMaterialFieldMapper
    {
        private static readonly Dictionary<string, string> AliasToCanonical =
            new Dictionary<string, string>(StringComparer.OrdinalIgnoreCase)
            {
                { "图号", "item_number" },
                { "drawingno", "item_number" },
                { "drawing_no", "item_number" },
                { "partnumber", "item_number" },
                { "part_number", "item_number" },
                { "itemnumber", "item_number" },
                { "item_number", "item_number" },
                { "名称", "name" },
                { "品名", "name" },
                { "name", "name" },
                { "description", "name" },
                { "材料", "material" },
                { "材质", "material" },
                { "material", "material" },
                { "规格", "specification" },
                { "规格型号", "specification" },
                { "物料规格", "specification" },
                { "spec", "specification" },
                { "specification", "specification" },
                { "物料类别", "material_category" },
                { "materialcategory", "material_category" },
                { "material_category", "material_category" },
                { "长", "length" },
                { "长度", "length" },
                { "length", "length" },
                { "宽", "width" },
                { "宽度", "width" },
                { "width", "width" },
                { "厚", "thickness" },
                { "厚度", "thickness" },
                { "thickness", "thickness" },
                { "外径", "outer_diameter" },
                { "outerdiameter", "outer_diameter" },
                { "outer_diameter", "outer_diameter" },
                { "壁厚", "wall_thickness" },
                { "wallthickness", "wall_thickness" },
                { "wall_thickness", "wall_thickness" },
                { "直径", "diameter" },
                { "diameter", "diameter" },
                { "毛坯尺寸", "blank_size" },
                { "blanksize", "blank_size" },
                { "blank_size", "blank_size" },
                { "热处理", "heat_treatment" },
                { "heattreatment", "heat_treatment" },
                { "heat_treatment", "heat_treatment" },
                // finishing/treatment R1 (#689): surface finish / coating -> finish.
                // Deliberately NOT the bare "表面" (surface) alias — too broad, would
                // mis-map unrelated surface-* fields.
                { "表面处理", "finish" },
                { "涂层", "finish" },
                { "finish", "finish" },
                { "coating", "finish" }
            };

        public bool AddField(Dictionary<string, object> fields, string rawKey, string value)
        {
            if (fields == null || string.IsNullOrWhiteSpace(rawKey) || string.IsNullOrWhiteSpace(value))
            {
                return false;
            }
            var key = CanonicalKey(rawKey);
            if (string.IsNullOrWhiteSpace(key))
            {
                return false;
            }
            fields[key] = value.Trim();
            return true;
        }

        public void ExtractTableCells(
            int rowCount,
            int columnCount,
            Func<int, int, string> getCellText,
            Dictionary<string, object> fields)
        {
            if (getCellText == null || fields == null)
            {
                return;
            }

            for (var row = 0; row < rowCount; row++)
            {
                for (var col = 0; col < columnCount; col++)
                {
                    var text = getCellText(row, col);
                    if (string.IsNullOrWhiteSpace(text))
                    {
                        continue;
                    }

                    var equalsIndex = text.IndexOf('=');
                    if (equalsIndex > 0)
                    {
                        AddField(fields, text.Substring(0, equalsIndex), text.Substring(equalsIndex + 1));
                        continue;
                    }

                    if (col + 1 < columnCount)
                    {
                        var value = getCellText(row, col + 1);
                        if (AddField(fields, text, value))
                        {
                            col++;
                        }
                    }
                }
            }
        }

        public int ApplyTableCells(
            int rowCount,
            int columnCount,
            Func<int, int, string> getCellText,
            Func<int, int, string, int> setCellText,
            Dictionary<string, string> normalized)
        {
            if (getCellText == null || setCellText == null || normalized == null)
            {
                return 0;
            }

            var updated = 0;
            for (var row = 0; row < rowCount; row++)
            {
                for (var col = 0; col < columnCount; col++)
                {
                    var text = getCellText(row, col);
                    if (string.IsNullOrWhiteSpace(text))
                    {
                        continue;
                    }

                    var equalsIndex = text.IndexOf('=');
                    if (equalsIndex > 0 && TryGetValue(normalized, text.Substring(0, equalsIndex), out var inlineValue))
                    {
                        updated += setCellText(row, col, text.Substring(0, equalsIndex) + "=" + inlineValue);
                        continue;
                    }

                    if (col + 1 < columnCount && TryGetValue(normalized, text, out var adjacentValue))
                    {
                        updated += setCellText(row, col + 1, adjacentValue);
                        col++;
                    }
                }
            }
            return updated;
        }

        public bool TryGetValue(
            Dictionary<string, string> normalized,
            string rawKey,
            out string value)
        {
            value = null;
            if (normalized == null || string.IsNullOrWhiteSpace(rawKey))
            {
                return false;
            }
            return normalized.TryGetValue(CanonicalKey(rawKey), out value);
        }

        public Dictionary<string, string> NormalizeInputFields(Dictionary<string, object> fields)
        {
            var normalized = new Dictionary<string, string>(StringComparer.OrdinalIgnoreCase);
            if (fields == null)
            {
                return normalized;
            }

            foreach (var pair in fields)
            {
                var key = CanonicalKey(pair.Key);
                var value = ValueToString(pair.Value);
                if (!string.IsNullOrWhiteSpace(key) && !string.IsNullOrWhiteSpace(value))
                {
                    normalized[key] = value;
                }
            }
            return normalized;
        }

        public string CanonicalKey(string raw)
        {
            if (string.IsNullOrWhiteSpace(raw))
            {
                return string.Empty;
            }
            var compact = raw.Trim()
                .Replace(" ", string.Empty)
                .Replace("\t", string.Empty)
                .Replace("-", "_")
                .Replace("：", string.Empty)
                .Replace(":", string.Empty)
                .ToLowerInvariant();

            if (AliasToCanonical.TryGetValue(compact, out var canonical))
            {
                return canonical;
            }
            return compact;
        }

        private static string ValueToString(object value)
        {
            if (value == null)
            {
                return string.Empty;
            }
            if (value is IFormattable formattable)
            {
                return formattable.ToString(null, CultureInfo.InvariantCulture);
            }
            return Convert.ToString(value, CultureInfo.InvariantCulture) ?? string.Empty;
        }
    }
}
