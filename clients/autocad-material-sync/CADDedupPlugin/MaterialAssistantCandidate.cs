using System.Collections.Generic;

namespace CADDedupPlugin
{
    /// <summary>
    /// SDK-free helpers for the assistant candidate payload (Dictionary-backed).
    /// Used by PLMMATASSIST to pick an existing item id safely. Phase 4 contract
    /// (§5.4): never infer an id from material number, description, or rendered
    /// text — only a usable "id" is accepted.
    /// </summary>
    public static class MaterialAssistantCandidate
    {
        /// <summary>
        /// Returns the candidate's "id" as a non-empty string, or null when the
        /// candidate is missing, has no id, or the id is null/blank.
        /// </summary>
        public static string ExtractItemId(Dictionary<string, object> candidate)
        {
            if (candidate == null)
            {
                return null;
            }
            if (!candidate.TryGetValue("id", out var idValue) || idValue == null)
            {
                return null;
            }
            var text = idValue.ToString();
            return string.IsNullOrWhiteSpace(text) ? null : text;
        }
    }
}
