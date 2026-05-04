import { describe, expect, it } from "vitest";
import { buildGroupedRows, buildUngroupedRows } from "./sourcesGrouping";
import type { Source } from "../types";

function src(overrides: Partial<Source>): Source {
  return {
    id: "id-" + (overrides.id ?? Math.random().toString(36).slice(2, 8)),
    name: "src",
    type: "smb",
    host_id: null,
    host: null,
    scan_schedule: null,
    preferred_pool: null,
    max_parallel_scanners: 1,
    last_scan_at: null,
    status: "online",
    created_at: "2026-05-04T00:00:00Z",
    updated_at: "2026-05-04T00:00:00Z",
    is_removable: false,
    is_reachable: null,
    last_reachable_at: null,
    last_reachability_check_at: null,
    ...overrides,
  };
}

describe("buildGroupedRows", () => {
  it("returns empty rows for empty input", () => {
    expect(buildGroupedRows([], new Set())).toEqual([]);
  });

  it("groups sources by host_id and emits header + cards per host", () => {
    const rows = buildGroupedRows(
      [
        src({ id: "a1", name: "Public",      host_id: "host-a", host: { id: "host-a", name: "alpha", type: "smb" } }),
        src({ id: "a2", name: "Engineering", host_id: "host-a", host: { id: "host-a", name: "alpha", type: "smb" } }),
        src({ id: "b1", name: "Backups",     host_id: "host-b", host: { id: "host-b", name: "beta",  type: "nfs" } }),
      ],
      new Set(),
    );
    // Expect: alpha header → 2 cards → beta header → 1 card.
    expect(rows.map((r) => r.kind)).toEqual([
      "header", "card", "card", "header", "card",
    ]);
    expect(rows[0]).toMatchObject({ kind: "header", hostName: "alpha", count: 2 });
    expect(rows[3]).toMatchObject({ kind: "header", hostName: "beta",  count: 1 });
  });

  it("sorts hosts alphabetically and sources within a host alphabetically", () => {
    const rows = buildGroupedRows(
      [
        src({ id: "z1", name: "zeta",  host_id: "h-z", host: { id: "h-z", name: "zeta-host",  type: "smb" } }),
        src({ id: "a1", name: "alpha", host_id: "h-a", host: { id: "h-a", name: "alpha-host", type: "smb" } }),
        src({ id: "a2", name: "beta",  host_id: "h-a", host: { id: "h-a", name: "alpha-host", type: "smb" } }),
      ],
      new Set(),
    );
    const headerNames = rows
      .filter((r) => r.kind === "header")
      .map((r) => (r as { hostName: string }).hostName);
    expect(headerNames).toEqual(["alpha-host", "zeta-host"]);

    // Within alpha-host, sources sorted by name: alpha, beta
    expect(rows.slice(1, 3).map((r) => (r as { source: Source }).source.name))
      .toEqual(["alpha", "beta"]);
  });

  it("buckets host_id=null sources under 'Direct sources' last", () => {
    const rows = buildGroupedRows(
      [
        src({ id: "loc", name: "local-fs",  host_id: null }),
        src({ id: "rem", name: "remote",    host_id: "h-1", host: { id: "h-1", name: "named-host", type: "smb" } }),
      ],
      new Set(),
    );
    const headers = rows.filter((r) => r.kind === "header") as Array<{ hostName: string; hostId: string | null }>;
    expect(headers[0].hostName).toBe("named-host");
    expect(headers[1].hostName).toBe("Direct sources");
    expect(headers[1].hostId).toBeNull();
  });

  it("omits card rows for collapsed groups but keeps the header", () => {
    const rows = buildGroupedRows(
      [
        src({ id: "a1", name: "Public", host_id: "host-a", host: { id: "host-a", name: "alpha", type: "smb" } }),
        src({ id: "b1", name: "Bckp",   host_id: "host-b", host: { id: "host-b", name: "beta",  type: "nfs" } }),
      ],
      new Set(["host-a"]),
    );
    // alpha is collapsed → header only; beta expanded → header + card
    expect(rows.map((r) => r.kind)).toEqual(["header", "header", "card"]);
  });

  it("falls back to 'Unknown host' when host_id is set but host is missing", () => {
    const rows = buildGroupedRows(
      [src({ id: "x", host_id: "ghost", host: null })],
      new Set(),
    );
    expect((rows[0] as { hostName: string }).hostName).toBe("Unknown host");
  });
});

describe("buildUngroupedRows", () => {
  it("emits one card row per source, no headers", () => {
    const rows = buildUngroupedRows([
      src({ id: "a", name: "alpha" }),
      src({ id: "b", name: "beta" }),
    ]);
    expect(rows.map((r) => r.kind)).toEqual(["card", "card"]);
    expect((rows[0] as { source: Source }).source.id).toContain("a");
  });
});

// v0.5.5 regression: TanStack `useVirtualizer` caches measured row sizes
// by the `getItemKey` value. The Sources page passes `rows[i].key` —
// so `card:<source-id>` must be IDENTICAL between grouped and ungrouped
// row streams, otherwise toggling Group-by reuses a stale measurement
// (e.g. the 36px header height) and the cards visually collapse on top
// of each other.
describe("row keys (virtualizer stability)", () => {
  const sources = [
    src({ id: "src-a", name: "alpha", host_id: "host-1", host: { id: "host-1", name: "fileserv01", type: "smb" } }),
    src({ id: "src-b", name: "beta",  host_id: "host-1", host: { id: "host-1", name: "fileserv01", type: "smb" } }),
    src({ id: "src-c", name: "gamma", host_id: null }),
  ];

  it("uses the stable shape `card:<source-id>` regardless of grouping", () => {
    const grouped = buildGroupedRows(sources, new Set());
    const ungrouped = buildUngroupedRows(sources);
    const cardKeysGrouped = grouped
      .filter((r) => r.kind === "card")
      .map((r) => r.key)
      .sort();
    const cardKeysUngrouped = ungrouped.map((r) => r.key).sort();
    // Same set of card keys in both modes — switching preserves per-
    // card identity so cached measurements survive the toggle.
    expect(cardKeysGrouped).toEqual(cardKeysUngrouped);
    expect(cardKeysGrouped).toEqual(["card:src-a", "card:src-b", "card:src-c"]);
  });

  it("never reuses a header key for a card slot", () => {
    const grouped = buildGroupedRows(sources, new Set());
    const headerKeys = new Set(grouped.filter((r) => r.kind === "header").map((r) => r.key));
    const cardKeys = new Set(grouped.filter((r) => r.kind === "card").map((r) => r.key));
    for (const k of headerKeys) expect(cardKeys.has(k)).toBe(false);
    for (const k of headerKeys) expect(k.startsWith("header:")).toBe(true);
    for (const k of cardKeys) expect(k.startsWith("card:")).toBe(true);
  });
});
