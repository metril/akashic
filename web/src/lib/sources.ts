import type { Source } from "../types";

/**
 * Compact, type-aware one-liner for the Sources list card. Replaces the
 * old `JSON.stringify(connection_config)` fallback that turned every
 * non-local source into a wall of JSON.
 *
 * Since v0.4.3 the api computes the summary server-side and ships it
 * as `source.summary` on the lean list endpoint. We prefer that when
 * present (saves the per-render computation + works without
 * connection_config in the payload). The local-compute path still
 * runs for the detail panel where the full Source object is loaded
 * and for any callers that haven't been updated yet.
 *
 * Returns a human-readable summary even when the config is partially
 * filled or has unfamiliar keys — fall back to the source name rather
 * than rendering nothing.
 */
export function formatSourceSummary(source: Source): string {
  // Prefer the server-rendered summary if the lean list endpoint
  // shipped one — saves work + works for sources whose
  // connection_config wasn't included in the payload.
  if (source.summary) {
    return source.summary;
  }
  const cfg = (source.connection_config ?? {}) as Record<string, unknown>;
  const get = (k: string) => (typeof cfg[k] === "string" ? (cfg[k] as string) : "");

  switch (source.type) {
    case "local":
      return get("path") || source.name;
    case "nfs": {
      const host = get("host");
      const exp = get("export_path");
      if (host && exp) return `${host}:${exp}`;
      return host || exp || source.name;
    }
    case "ssh": {
      const user = get("username");
      const host = get("host");
      const portRaw = cfg.port;
      const port = typeof portRaw === "number" && portRaw !== 22 ? `:${portRaw}` : "";
      if (user && host) return `${user}@${host}${port}`;
      return host || source.name;
    }
    case "smb": {
      const host = get("host");
      const share = get("share");
      if (host && share) return `\\\\${host}\\${share}`;
      return host || source.name;
    }
    case "s3": {
      const bucket = get("bucket");
      const region = get("region");
      const endpoint = get("endpoint");
      if (endpoint) return `${endpoint}/${bucket}`;
      if (bucket && region) return `${bucket} (${region})`;
      return bucket || source.name;
    }
    default:
      return source.name;
  }
}
