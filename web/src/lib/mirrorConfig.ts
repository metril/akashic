import type { SourceType } from "../components/sources/sourceTypes";

export type MirrorMapping = {
  source_id: string;
  remote_prefix: string;
  local_prefix: string;
};

/**
 * Client-side check of a backing source's `mirror_of` list. `targetTypes`
 * maps target source ids to their type; when a target's type is known the
 * paperless (empty remote prefix) and immich (rooted remote prefix) rules
 * apply. The server remains authoritative (400 on invalid, 404 unknown).
 */
export function validateMirrorOf(
  list: MirrorMapping[],
  backingType: SourceType,
  targetTypes?: ReadonlyMap<string, SourceType>,
): string | null {
  const seen = new Set<string>();
  for (const m of list) {
    if (!m.source_id) return "Pick a target source";
    const target = targetTypes?.get(m.source_id);
    if (target === "paperless") {
      if (m.remote_prefix !== "") return "Paperless mappings use an empty remote prefix";
    } else if (
      (m.remote_prefix !== "" || target !== undefined) &&
      !m.remote_prefix.startsWith("/")
    ) {
      return "Remote prefix must start with /";
    }
    if (backingType !== "s3" && !m.local_prefix.startsWith("/")) {
      return "Local prefix must start with /";
    }
    const key = `${m.source_id}\0${m.remote_prefix}\0${m.local_prefix}`;
    if (seen.has(key)) return "Duplicate mapping";
    seen.add(key);
  }
  return null;
}
