import { describe, it, expect } from "vitest";
import type { ACL, PosixACL, PosixACE, NfsV4ACL, NfsV4ACE } from "../types";
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

const nfsAce = (
  principal: string,
  ace_type: NfsV4ACE["ace_type"],
  mask: string[],
  flags: string[] = [],
): NfsV4ACE => ({ principal, ace_type, mask, flags });

const nfs = (entries: NfsV4ACE[]): NfsV4ACL => ({ type: "nfsv4", entries });

describe("diffACL — NFSv4", () => {
  it("reports added ACE", () => {
    const prev = nfs([nfsAce("OWNER@", "allow", ["read_data"])]);
    const curr = nfs([
      nfsAce("OWNER@", "allow", ["read_data"]),
      nfsAce("alice@dom", "allow", ["read_data", "write_data"]),
    ]);
    expect(diffACL(prev, curr)).toEqual([
      { kind: "added", summary: "alice@dom allow read_data,write_data" },
    ]);
  });

  it("reports removed ACE", () => {
    const prev = nfs([
      nfsAce("OWNER@", "allow", ["read_data"]),
      nfsAce("alice@dom", "deny", ["write_data"]),
    ]);
    const curr = nfs([nfsAce("OWNER@", "allow", ["read_data"])]);
    expect(diffACL(prev, curr)).toEqual([
      { kind: "removed", summary: "alice@dom deny write_data" },
    ]);
  });

  it("reports modified ACE when mask or flags change for same (principal, type)", () => {
    const prev = nfs([nfsAce("alice@dom", "allow", ["read_data"])]);
    const curr = nfs([nfsAce("alice@dom", "allow", ["read_data", "write_data"])]);
    expect(diffACL(prev, curr)).toEqual([
      { kind: "modified", summary: "alice@dom allow read_data → read_data,write_data" },
    ]);
  });

  it("reports reorder when kept ACEs swap order", () => {
    const prev = nfs([
      nfsAce("alice@dom", "deny", ["write_data"]),
      nfsAce("EVERYONE@", "allow", ["read_data"]),
    ]);
    const curr = nfs([
      nfsAce("EVERYONE@", "allow", ["read_data"]),
      nfsAce("alice@dom", "deny", ["write_data"]),
    ]);
    expect(diffACL(prev, curr)).toEqual([
      { kind: "reordered", summary: "ACE order changed (significant for evaluation)" },
    ]);
  });

  it("does not report reorder when add/remove changes naturally shift positions", () => {
    const prev = nfs([
      nfsAce("alice@dom", "deny", ["write_data"]),
      nfsAce("EVERYONE@", "allow", ["read_data"]),
    ]);
    const curr = nfs([
      nfsAce("alice@dom", "deny", ["write_data"]),
      nfsAce("bob@dom", "allow", ["read_data"]),
      nfsAce("EVERYONE@", "allow", ["read_data"]),
    ]);
    // alice and EVERYONE are still in the same relative order.
    expect(diffACL(prev, curr)).toEqual([
      { kind: "added", summary: "bob@dom allow read_data" },
    ]);
  });
});

import type { NtACE, NtACL, NtPrincipal } from "../types";

const ntPrincipal = (sid: string, name = ""): NtPrincipal => ({ sid, name });

const ntAce = (
  sid: string,
  name: string,
  ace_type: NtACE["ace_type"],
  mask: string[],
  flags: string[] = [],
): NtACE => ({ sid, name, ace_type, mask, flags });

const nt = (
  entries: NtACE[],
  owner: NtPrincipal | null = ntPrincipal("S-1-5-18", "SYSTEM"),
  group: NtPrincipal | null = ntPrincipal("S-1-5-32-544", "Administrators"),
): NtACL => ({ type: "nt", owner, group, control: [], entries });

describe("diffACL — NT", () => {
  it("reports owner_changed (display name when present, else SID)", () => {
    const prev = nt([], ntPrincipal("S-1-5-18", "SYSTEM"));
    const curr = nt([], ntPrincipal("S-1-5-21-100-100-100-500", "DOM\\Administrator"));
    expect(diffACL(prev, curr)).toContainEqual({
      kind: "owner_changed",
      from: "SYSTEM",
      to: "DOM\\Administrator",
    });
  });

  it("falls back to SID when owner has no name", () => {
    const prev = nt([], ntPrincipal("S-1-5-18", ""));
    const curr = nt([], ntPrincipal("S-1-5-19", ""));
    expect(diffACL(prev, curr)).toContainEqual({
      kind: "owner_changed",
      from: "S-1-5-18",
      to: "S-1-5-19",
    });
  });

  it("reports group_changed", () => {
    const prev = nt([], undefined, ntPrincipal("S-1-5-32-544", "Administrators"));
    const curr = nt([], undefined, ntPrincipal("S-1-5-32-545", "Users"));
    expect(diffACL(prev, curr)).toContainEqual({
      kind: "group_changed",
      from: "Administrators",
      to: "Users",
    });
  });

  it("reports added ACE keyed on (sid, ace_type)", () => {
    const prev = nt([ntAce("S-1-1-0", "Everyone", "allow", ["READ_DATA"])]);
    const curr = nt([
      ntAce("S-1-1-0", "Everyone", "allow", ["READ_DATA"]),
      ntAce("S-1-5-32-544", "Administrators", "allow", ["GENERIC_ALL"]),
    ]);
    expect(diffACL(prev, curr)).toContainEqual({
      kind: "added",
      summary: "Administrators allow GENERIC_ALL",
    });
  });

  it("scopes inherited ACE changes with [inherited]", () => {
    const prev = nt([ntAce("S-1-1-0", "Everyone", "allow", ["READ_DATA"], ["inherited"])]);
    const curr = nt([]);
    expect(diffACL(prev, curr)).toContainEqual({
      kind: "removed",
      scope: "inherited",
      summary: "Everyone allow READ_DATA [inherited]",
    });
  });

  it("does not double-report owner change as 'modified' or as type_changed", () => {
    const prev = nt([], ntPrincipal("S-1-5-18", "SYSTEM"));
    const curr = nt([], ntPrincipal("S-1-5-19", "LOCAL SERVICE"));
    expect(diffACL(prev, curr)).toEqual([
      { kind: "owner_changed", from: "SYSTEM", to: "LOCAL SERVICE" },
    ]);
  });
});

import type { S3ACL, S3Grant, S3Owner } from "../types";

const s3Owner = (id: string, display_name = ""): S3Owner => ({ id, display_name });

const grant = (
  grantee_type: S3Grant["grantee_type"],
  grantee_id: string,
  permission: S3Grant["permission"],
  grantee_name = "",
): S3Grant => ({ grantee_type, grantee_id, permission, grantee_name });

const s3 = (grants: S3Grant[], owner: S3Owner | null = s3Owner("acct-1", "owner")): S3ACL => ({
  type: "s3",
  owner,
  grants,
});

describe("diffACL — S3", () => {
  it("reports owner_changed using display_name when present, else id", () => {
    const prev = s3([], s3Owner("acct-1", "Team A"));
    const curr = s3([], s3Owner("acct-2", ""));
    expect(diffACL(prev, curr)).toContainEqual({
      kind: "owner_changed",
      from: "Team A",
      to: "acct-2",
    });
  });

  it("reports added grant", () => {
    const prev = s3([grant("canonical_user", "acct-1", "FULL_CONTROL")]);
    const curr = s3([
      grant("canonical_user", "acct-1", "FULL_CONTROL"),
      grant("group", "AllUsers", "READ"),
    ]);
    expect(diffACL(prev, curr)).toContainEqual({
      kind: "added",
      summary: "group:AllUsers READ",
    });
  });

  it("reports removed grant", () => {
    const prev = s3([
      grant("canonical_user", "acct-1", "FULL_CONTROL"),
      grant("group", "AllUsers", "READ"),
    ]);
    const curr = s3([grant("canonical_user", "acct-1", "FULL_CONTROL")]);
    expect(diffACL(prev, curr)).toContainEqual({
      kind: "removed",
      summary: "group:AllUsers READ",
    });
  });

  it("ignores ordering of grants", () => {
    const prev = s3([
      grant("canonical_user", "a", "FULL_CONTROL"),
      grant("group", "AllUsers", "READ"),
    ]);
    const curr = s3([
      grant("group", "AllUsers", "READ"),
      grant("canonical_user", "a", "FULL_CONTROL"),
    ]);
    expect(diffACL(prev, curr)).toEqual([]);
  });

  it("treats permission change as remove+add (different key)", () => {
    const prev = s3([grant("group", "AllUsers", "READ")]);
    const curr = s3([grant("group", "AllUsers", "WRITE")]);
    const result = diffACL(prev, curr);
    expect(result).toContainEqual({ kind: "removed", summary: "group:AllUsers READ" });
    expect(result).toContainEqual({ kind: "added", summary: "group:AllUsers WRITE" });
  });
});
