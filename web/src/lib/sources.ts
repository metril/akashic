import type { Source } from "../types";

/**
 * Compact, type-aware one-liner for the Sources list card. Replaces the
 * old `JSON.stringify(connection_config)` fallback that turned every
 * non-local source into a wall of JSON.
 *
 * Returns a human-readable summary even when the config is partially
 * filled or has unfamiliar keys — fall back to the source name rather
 * than rendering nothing.
 */
export function formatSourceSummary(source: Source): string {
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
