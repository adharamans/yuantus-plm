using System;
using System.Collections.Generic;

namespace SolidWorksMaterialSync
{
    /// <summary>
    /// SDK-free SolidWorks key mapping to canonical CAD Material Sync fields.
    /// </summary>
    public sealed class SolidWorksMaterialFieldMapper
    {
        private static readonly Dictionary<string, string> AliasToCanonical =
            new Dictionary<string, string>(StringComparer.OrdinalIgnoreCase)
            {
                { "SW-Part Number", "item_number" },
                { "SW-PartNumber", "item_number" },
                { "Part Number", "item_number" },
                { "PartNumber", "item_number" },
                { "SW-Description", "name" },
                { "Description", "name" },
                { "SW-Material", "material" },
                { "Material", "material" },
                { "SW-Specification", "specification" },
                { "Specification", "specification" },
                { "SW-MaterialCategory", "material_category" },
                { "MaterialCategory", "material_category" },
                { "SW-Length", "length" },
                { "Length", "length" },
                { "SW-Width", "width" },
                { "Width", "width" },
                { "SW-Thickness", "thickness" },
                { "Thickness", "thickness" },
                { "SW-OuterDiameter", "outer_diameter" },
                { "OuterDiameter", "outer_diameter" },
                { "SW-WallThickness", "wall_thickness" },
                { "WallThickness", "wall_thickness" },
                { "SW-Diameter", "diameter" },
                { "Diameter", "diameter" },
                { "SW-BlankSize", "blank_size" },
                { "BlankSize", "blank_size" },
                { "SW-HeatTreatment", "heat_treatment" },
                { "HeatTreatment", "heat_treatment" },
                // finishing/treatment R1 (#689): surface finish / coating -> finish.
                { "SW-Coating", "finish" },
                { "Coating", "finish" },
                { "SW-Finish", "finish" },
                { "Finish", "finish" }
            };

        public bool AddField(Dictionary<string, object> fields, string rawKey, string value)
        {
            if (fields == null || string.IsNullOrWhiteSpace(rawKey) || string.IsNullOrWhiteSpace(value))
            {
                return false;
            }

            var canonicalKey = CanonicalKey(rawKey);
            if (string.IsNullOrWhiteSpace(canonicalKey))
            {
                return false;
            }

            fields[canonicalKey] = value.Trim();
            return true;
        }

        public void ExtractTableCells(IReadOnlyList<string> row, Dictionary<string, object> fields)
        {
            if (row == null || fields == null)
            {
                return;
            }

            for (var index = 0; index < row.Count; index++)
            {
                var cell = row[index];
                if (string.IsNullOrWhiteSpace(cell))
                {
                    continue;
                }

                var equalsIndex = cell.IndexOf('=');
                if (equalsIndex > 0)
                {
                    AddField(fields, cell.Substring(0, equalsIndex), cell.Substring(equalsIndex + 1));
                    continue;
                }

                if (index + 1 < row.Count && AddField(fields, cell, row[index + 1]))
                {
                    index++;
                }
            }
        }

        public string CanonicalKey(string rawKey)
        {
            if (string.IsNullOrWhiteSpace(rawKey))
            {
                return string.Empty;
            }

            var key = StripSolidWorksScope(rawKey.Trim());
            if (AliasToCanonical.TryGetValue(key, out var canonical))
            {
                return canonical;
            }

            return string.Empty;
        }

        private static string StripSolidWorksScope(string rawKey)
        {
            var atIndex = rawKey.IndexOf('@');
            if (atIndex > 0)
            {
                return rawKey.Substring(0, atIndex);
            }
            return rawKey;
        }
    }
}

