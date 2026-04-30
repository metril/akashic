import { describe, expect, it } from "vitest";
import type { NtACE } from "../types";
import { dedupeAces } from "./aclDedupe";

// Builder so test cases stay readable. NtACE has more fields than the
// dedupe key; the helper only inspects type/sid/flags/mask, so the
// other fields are filled with sensible defaults.
function ace(partial: Partial<NtACE> = {}): NtACE {
  return {
    sid: partial.sid ?? "S-1-5-21-1-2-3-1001",
    name: partial.name ?? "",
    ace_type: partial.ace_type ?? "allow",
    flags: partial.flags ?? [],
    mask: partial.mask ?? [],
  };
}

describe("dedupeAces", () => {
  it("returns empty for empty input", () => {
    expect(dedupeAces([])).toEqual([]);
  });

  it("collapses two pixel-perfect identical ACEs", () => {
    const a = ace({ sid: "S-1-5-21-X-1001", mask: ["READ_DATA"], flags: ["inherited"] });
    const b = ace({ sid: "S-1-5-21-X-1001", mask: ["READ_DATA"], flags: ["inherited"] });
    const out = dedupeAces([a, b]);
    expect(out).toHaveLength(1);
    expect(out[0].count).toBe(2);
    expect(out[0].ace.sid).toBe("S-1-5-21-X-1001");
  });

  it("keeps allow and deny separate even when other fields match", () => {
    const allowAce = ace({ ace_type: "allow", mask: ["READ_DATA"] });
    const denyAce = ace({ ace_type: "deny", mask: ["READ_DATA"] });
    const out = dedupeAces([allowAce, denyAce]);
    expect(out).toHaveLength(2);
    expect(out.every((g) => g.count === 1)).toBe(true);
  });

  it("collapses regardless of flag insertion order", () => {
    // Real-world quirk: some Windows tools serialize OI+CI in either
    // order. Dedup should normalize via sort.
    const a = ace({ flags: ["object_inherit", "container_inherit"] });
    const b = ace({ flags: ["container_inherit", "object_inherit"] });
    const out = dedupeAces([a, b]);
    expect(out).toHaveLength(1);
    expect(out[0].count).toBe(2);
  });

  it("collapses regardless of mask insertion order", () => {
    const a = ace({ mask: ["READ_DATA", "WRITE_DATA"] });
    const b = ace({ mask: ["WRITE_DATA", "READ_DATA"] });
    const out = dedupeAces([a, b]);
    expect(out).toHaveLength(1);
    expect(out[0].count).toBe(2);
  });

  it("keys on SID, not name — resolved + unresolved twins collapse", () => {
    // The on-demand resolver might fill in `name` for one copy and
    // not the other (race between query completion and re-render).
    // Dedup must still collapse them so the (×N) count is stable.
    const a = ace({ sid: "S-1-5-21-X-1001", name: "" });
    const b = ace({ sid: "S-1-5-21-X-1001", name: "OLYMPOS\\Bob" });
    const out = dedupeAces([a, b]);
    expect(out).toHaveLength(1);
    expect(out[0].count).toBe(2);
  });

  it("keeps different SIDs separate", () => {
    const a = ace({ sid: "S-1-5-21-X-1001" });
    const b = ace({ sid: "S-1-5-21-X-1002" });
    const out = dedupeAces([a, b]);
    expect(out).toHaveLength(2);
  });

  it("preserves first-encounter order across distinct groups", () => {
    // ACL evaluation order matters (deny before allow precedence).
    // Reordering distinct groups would mislead an audit reader.
    const groupA = ace({ sid: "S-1-A" });
    const groupB = ace({ sid: "S-1-B" });
    const groupC = ace({ sid: "S-1-C" });
    const out = dedupeAces([groupA, groupB, groupA, groupC, groupB]);
    expect(out.map((g) => g.ace.sid)).toEqual(["S-1-A", "S-1-B", "S-1-C"]);
    expect(out.map((g) => g.count)).toEqual([2, 2, 1]);
  });

  it("recreates the user's reported pattern (4 inherited principals doubled)", () => {
    // Verbatim shape from the entry the user reported on
    // /browse?source=…&path=86%2FSeason+1: each inherited principal
    // appears twice with bit-for-bit identical mask + flags. The
    // user should see 4 rows, each tagged ×2.
    const fullMask = [
      "READ_DATA", "WRITE_DATA", "APPEND_DATA", "READ_EA", "WRITE_EA",
      "EXECUTE", "DELETE_CHILD", "READ_ATTRIBUTES", "WRITE_ATTRIBUTES",
      "DELETE", "READ_CONTROL", "WRITE_DAC", "WRITE_OWNER", "SYNCHRONIZE",
    ];
    const inheritedFlags = ["object_inherit", "container_inherit", "inherited"];
    const principals = ["27106", "25118", "1115", "512"];
    const aces = principals.flatMap((rid) => [
      ace({ sid: `S-1-5-21-X-${rid}`, mask: fullMask, flags: inheritedFlags }),
      ace({ sid: `S-1-5-21-X-${rid}`, mask: fullMask, flags: inheritedFlags }),
    ]);
    const out = dedupeAces(aces);
    expect(out).toHaveLength(4);
    expect(out.every((g) => g.count === 2)).toBe(true);
  });
});
