import { describe, it, expect } from "vitest";
import type { ACL } from "../types";
import { diffACL } from "./aclDiff";

describe("diffACL — dispatcher", () => {
  it("returns [] when both are null", () => {
    expect(diffACL(null, null)).toEqual([]);
  });

  it("returns [] when prev and curr are deeply equal", () => {
    const a: ACL = { type: "posix", entries: [{ tag: "user_obj", qualifier: "", perms: "rwx" }], default_entries: null };
    const b: ACL = { type: "posix", entries: [{ tag: "user_obj", qualifier: "", perms: "rwx" }], default_entries: null };
    expect(diffACL(a, b)).toEqual([]);
  });

  it("reports type_changed when prev and curr differ in type", () => {
    const a: ACL = { type: "posix", entries: [], default_entries: null };
    const b: ACL = { type: "nfsv4", entries: [] };
    expect(diffACL(a, b)).toEqual([{ kind: "type_changed", from: "posix", to: "nfsv4" }]);
  });

  it("reports type_changed (none → posix) when prev is null and curr is set", () => {
    const b: ACL = { type: "posix", entries: [], default_entries: null };
    expect(diffACL(null, b)).toEqual([{ kind: "type_changed", from: "none", to: "posix" }]);
  });

  it("reports type_changed (posix → none) when prev is set and curr is null", () => {
    const a: ACL = { type: "posix", entries: [], default_entries: null };
    expect(diffACL(a, null)).toEqual([{ kind: "type_changed", from: "posix", to: "none" }]);
  });
});
