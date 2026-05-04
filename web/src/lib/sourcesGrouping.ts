import type { Source } from "../types";

/**
 * One renderable row in the Sources page virtualizer. The page used
 * to render `Source[]` directly; v0.5.4 wraps them in a typed union
 * so the same virtualizer can intersperse host header rows that
 * group all the cards belonging to one host.
 *
 * Exported as a standalone module (rather than inlined in Sources.tsx)
 * so the row-building logic can be vitest-tested without dragging in
 * the whole React tree.
 */
export type SourceRow =
  | {
      kind: "header";
      key: string;
      hostId: string | null;
      hostName: string;
      hostType: string | null;
      count: number;
      /** Number of attached sources whose credentials differ from the
       * host default — either via `credential_profile_id` set on the
       * source, or inline credential keys on the source's
       * connection_config. Used by HostHeader to render an indicator. */
      overrideCount: number;
    }
  | { kind: "card"; key: string; source: Source };

/** localStorage key for the "Group by" toggle. */
export const GROUP_BY_KEY = "sources-group-by";

export type GroupBy = "host" | "none";

export function readGroupByPref(): GroupBy {
  try {
    const v = localStorage.getItem(GROUP_BY_KEY);
    return v === "none" ? "none" : "host";
  } catch {
    return "host";
  }
}

export function writeGroupByPref(v: GroupBy): void {
  try {
    localStorage.setItem(GROUP_BY_KEY, v);
  } catch {
    // Storage may be blocked; preference resets next session.
  }
}

/** localStorage key prefix for per-host collapse state. */
const COLLAPSE_KEY_PREFIX = "sources-host-collapsed:";

export function readHostCollapsed(hostKey: string): boolean {
  try {
    return localStorage.getItem(COLLAPSE_KEY_PREFIX + hostKey) === "1";
  } catch {
    return false;
  }
}

export function writeHostCollapsed(hostKey: string, collapsed: boolean): void {
  try {
    if (collapsed) localStorage.setItem(COLLAPSE_KEY_PREFIX + hostKey, "1");
    else localStorage.removeItem(COLLAPSE_KEY_PREFIX + hostKey);
  } catch {
    // ignore
  }
}

/**
 * Convert a flat `Source[]` into a row stream grouped by host.
 *
 * Sources with `host_id == null` (i.e. `local` sources) bucket under
 * a synthetic "Direct sources" header keyed `__none__`. Host buckets
 * sort alphabetically by host.name; sources within a bucket sort by
 * source.name. Host headers carry the bucket's source count so the
 * UI can render "Engineering NAS · 12 shares" without a recount.
 *
 * Collapsed groups omit the card rows (so the virtualizer's total
 * height shrinks). Pass an empty Set to render fully expanded.
 */
export function buildGroupedRows(
  sources: readonly Source[],
  collapsed: ReadonlySet<string>,
): SourceRow[] {
  if (sources.length === 0) return [];

  // Bucket by host_id. Use "__none__" as the sentinel key for the
  // null bucket so it sorts deterministically next to real ids.
  const buckets = new Map<string, Source[]>();
  for (const s of sources) {
    const key = s.host_id ?? "__none__";
    const list = buckets.get(key);
    if (list) list.push(s);
    else buckets.set(key, [s]);
  }

  // Build per-bucket header info, sorted by host name (null bucket
  // last so the named groups come first).
  type BucketHeader = {
    key: string;
    hostId: string | null;
    hostName: string;
    hostType: string | null;
    sources: Source[];
  };
  const headers: BucketHeader[] = [];
  for (const [key, list] of buckets) {
    if (key === "__none__") {
      headers.push({
        key,
        hostId: null,
        hostName: "Direct sources",
        hostType: null,
        sources: list,
      });
    } else {
      // Pick the first source's inlined host shape — they all share
      // the same host_id so they all carry the same `host`.
      const firstWithHost = list.find((s) => s.host != null);
      const host = firstWithHost?.host;
      headers.push({
        key,
        hostId: key,
        hostName: host?.name ?? "Unknown host",
        hostType: host?.type ?? null,
        sources: list,
      });
    }
  }
  headers.sort((a, b) => {
    if (a.hostId === null) return 1;
    if (b.hostId === null) return -1;
    return a.hostName.localeCompare(b.hostName);
  });

  const rows: SourceRow[] = [];
  for (const h of headers) {
    rows.push({
      kind: "header",
      key: `header:${h.key}`,
      hostId: h.hostId,
      hostName: h.hostName,
      hostType: h.hostType,
      count: h.sources.length,
      overrideCount: countCredentialOverrides(h.sources),
    });
    if (collapsed.has(h.key)) continue;
    const sorted = [...h.sources].sort((a, b) => a.name.localeCompare(b.name));
    for (const s of sorted) {
      rows.push({ kind: "card", key: `card:${s.id}`, source: s });
    }
  }
  return rows;
}

/**
 * v0.5.9 — count sources within a host group that override the host's
 * effective credentials. A source overrides when it has its own
 * `credential_profile_id` set OR when its `connection_config` carries
 * credential-shaped keys (username, password, key_path, etc.). The
 * lean list endpoint usually omits `connection_config`, so this
 * function falls back to checking just the profile id in that case —
 * which under-counts inline overrides. The HostHeader copy reflects
 * "at least N of M".
 */
const _CRED_KEYS = new Set([
  "username", "password", "key_path", "key_passphrase",
  "private_key", "access_key_id", "secret_access_key",
  "krb5_principal", "auth_uid", "auth_gid",
]);

export function countCredentialOverrides(sources: readonly Source[]): number {
  let n = 0;
  for (const s of sources) {
    if (s.credential_profile_id) {
      n += 1;
      continue;
    }
    const cfg = s.connection_config;
    if (cfg) {
      for (const k of Object.keys(cfg)) {
        if (_CRED_KEYS.has(k)) {
          n += 1;
          break;
        }
      }
    }
  }
  return n;
}

/** Convert a flat list to ungrouped row stream — one card per row. */
export function buildUngroupedRows(sources: readonly Source[]): SourceRow[] {
  return sources.map<SourceRow>((s) => ({
    kind: "card",
    key: `card:${s.id}`,
    source: s,
  }));
}
