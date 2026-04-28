import { describe, it, expect } from "vitest";
import type { ACL, PosixACL, PosixACE } from "../types";
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

const posix = (entries: PosixACE[], def: PosixACE[] | null = null): PosixACL => ({
  type: "posix",
  entries,
  default_entries: def,
});

describe("diffACL — POSIX", () => {
  it("reports added entries", () => {
    const prev = posix([{ tag: "user_obj", qualifier: "", perms: "rwx" }]);
    const curr = posix([
      { tag: "user_obj", qualifier: "", perms: "rwx" },
      { tag: "user", qualifier: "alice", perms: "r-x" },
    ]);
    expect(diffACL(prev, curr)).toEqual([
      { kind: "added", summary: "user:alice r-x" },
    ]);
  });

  it("reports removed entries", () => {
    const prev = posix([
      { tag: "user_obj", qualifier: "", perms: "rwx" },
      { tag: "user", qualifier: "alice", perms: "r-x" },
    ]);
    const curr = posix([{ tag: "user_obj", qualifier: "", perms: "rwx" }]);
    expect(diffACL(prev, curr)).toEqual([
      { kind: "removed", summary: "user:alice r-x" },
    ]);
  });

  it("reports modified entries with perms diff", () => {
    const prev = posix([{ tag: "user", qualifier: "alice", perms: "r--" }]);
    const curr = posix([{ tag: "user", qualifier: "alice", perms: "rwx" }]);
    expect(diffACL(prev, curr)).toEqual([
      { kind: "modified", summary: "user:alice r-- → rwx" },
    ]);
  });

  it("ignores ordering", () => {
    const prev = posix([
      { tag: "user", qualifier: "alice", perms: "r--" },
      { tag: "user", qualifier: "bob", perms: "rw-" },
    ]);
    const curr = posix([
      { tag: "user", qualifier: "bob", perms: "rw-" },
      { tag: "user", qualifier: "alice", perms: "r--" },
    ]);
    expect(diffACL(prev, curr)).toEqual([]);
  });

  it("scopes default-ACL changes with [default]", () => {
    const prev = posix(
      [{ tag: "user_obj", qualifier: "", perms: "rwx" }],
      [{ tag: "user_obj", qualifier: "", perms: "rwx" }],
    );
    const curr = posix(
      [{ tag: "user_obj", qualifier: "", perms: "rwx" }],
      [
        { tag: "user_obj", qualifier: "", perms: "rwx" },
        { tag: "user", qualifier: "nobody", perms: "r-x" },
      ],
    );
    expect(diffACL(prev, curr)).toEqual([
      { kind: "added", scope: "default", summary: "user:nobody r-x" },
    ]);
  });

  it("treats null and empty default_entries as equivalent", () => {
    const prev = posix([{ tag: "user_obj", qualifier: "", perms: "rwx" }], []);
    const curr = posix([{ tag: "user_obj", qualifier: "", perms: "rwx" }]);
    expect(diffACL(prev, curr)).toEqual([]);
  });

  it("handles tags without qualifiers (mask, other, *_obj)", () => {
    const prev = posix([{ tag: "mask", qualifier: "", perms: "rwx" }]);
    const curr = posix([{ tag: "mask", qualifier: "", perms: "r-x" }]);
    expect(diffACL(prev, curr)).toEqual([
      { kind: "modified", summary: "mask rwx → r-x" },
    ]);
  });
});
