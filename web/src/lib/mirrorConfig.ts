import type { SourceType } from "../components/sources/sourceTypes";

export type MirrorMapping = {
  source_id: string;
  remote_prefix: string;
  local_prefix: string;
};

/**
 * Client-side check of an Immich/Paperless source's `mirrors` list.
 * `backingTypes` maps backing source ids to their type; when a backing
 * source's type is known, non-s3 backings need a rooted local prefix.
 * The server remains authoritative (400 on invalid, 404 unknown).
 */
export function validateMirrors(
  list: MirrorMapping[],
  appType: "immich" | "paperless",
  backingTypes?: ReadonlyMap<string, SourceType>,
): string | null {
  const seen = new Set<string>();
  for (const m of list) {
    if (!m.source_id) return "Pick a backing source";
    if (appType === "paperless") {
      if (m.remote_prefix !== "") return "Paperless mappings use an empty remote prefix";
    } else if (!m.remote_prefix.startsWith("/")) {
      return "Remote prefix must start with /";
    }
    const backing = backingTypes?.get(m.source_id);
    if (backing !== undefined && backing !== "s3" && !m.local_prefix.startsWith("/")) {
      return "Local prefix must start with /";
    }
    const key = `${m.source_id}\0${m.remote_prefix}\0${m.local_prefix}`;
    if (seen.has(key)) return "Duplicate mapping";
    seen.add(key);
  }
  return null;
}
