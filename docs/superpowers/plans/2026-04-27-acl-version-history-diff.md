# Phase 10 — Per-type ACL Version-History Diff Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the literal `Changed: acl` label in `EntryDetail`'s version history with a structured per-type ACL diff that reports added/removed/modified ACEs, owner/group changes, and ACL-type changes — with semantics matched to each ACL model (POSIX = key-set, NFSv4/NT = ordered, S3 = key-set).

**Architecture:** Pure-function diff library `web/src/lib/aclDiff.ts` exporting `diffACL(prev, curr): ACLDiffItem[]`. Per-type strategies dispatched by the discriminator `acl.type`: POSIX uses a `(tag, qualifier)` keyed set diff (also runs over `default_entries`); NFSv4 and NT use an LCS-based ordered diff that explicitly reports inserts/removals AND reorders, plus first-class owner/group items for NT; S3 uses a `(grantee_type, grantee_id, permission)` keyed set diff plus first-class owner change. A presentational React component `web/src/components/acl/ACLDiff.tsx` renders the resulting items with per-kind icons and colors. `EntryDetail.tsx` swaps the `if … changes.push("acl")` branch for the new component while leaving the other field-change labels (mode/ownership/content/etc.) untouched.

**Tech Stack:** TypeScript, React 18, Tailwind, Vitest (added in this phase as the test framework — Vite-native, zero-config). No new runtime dependencies.

---

## File structure

**Create**
- `web/src/lib/aclDiff.ts` — pure `diffACL()` and per-type strategies. No React imports.
- `web/src/lib/aclDiff.test.ts` — vitest unit tests covering every strategy.
- `web/src/components/acl/ACLDiff.tsx` — presentational component rendering `ACLDiffItem[]`.
- `web/vitest.config.ts` — minimal config.

**Edit**
- `web/package.json` — add `vitest` dev dep + `"test"` script.
- `web/src/components/EntryDetail.tsx` — replace inline `acl` change detection with `<ACLDiff prev={…} curr={…} />` rendered inside the version-history list item; leave other change labels intact.

**No deletes.** No backend changes. No type changes (the existing `ACL` discriminated union in `web/src/types/index.ts` is sufficient).

---

## Task 1 — Add Vitest test framework

Vitest is the natural fit (Vite-native, ESM, TypeScript out of the box). One-time setup so subsequent tasks can write tests.

**Files:**
- Modify: `web/package.json`
- Create: `web/vitest.config.ts`

- [ ] **Step 1: Install vitest as a dev dependency**

Run inside the existing web container so we don't pollute the host:

```bash
docker compose -f docker-compose.yml exec -T web npm install --save-dev vitest@^2.1.0 @vitest/ui@^2.1.0
```

Expected: `package.json` and `package-lock.json` updated. If the web container isn't running, `docker compose run --rm web npm install --save-dev vitest@^2.1.0 @vitest/ui@^2.1.0` is the fallback.

- [ ] **Step 2: Add a `test` script to `package.json`**

Edit `web/package.json` `scripts` block to add:

```json
    "test": "vitest run",
    "test:watch": "vitest"
```

The full `scripts` block becomes:

```json
  "scripts": {
    "dev": "vite",
    "build": "tsc && vite build",
    "preview": "vite preview",
    "test": "vitest run",
    "test:watch": "vitest"
  },
```

- [ ] **Step 3: Create `vitest.config.ts`**

Create `web/vitest.config.ts`:

```ts
import { defineConfig } from "vitest/config";

export default defineConfig({
  test: {
    environment: "node",
    include: ["src/**/*.test.ts", "src/**/*.test.tsx"],
  },
});
```

`environment: "node"` is correct — `aclDiff.ts` is pure logic, no DOM needed. (If we later test React components we can add `jsdom`.)

- [ ] **Step 4: Smoke-test that vitest runs**

Create a temporary `web/src/lib/_vitest_smoke.test.ts`:

```ts
import { describe, it, expect } from "vitest";

describe("vitest smoke", () => {
  it("runs", () => {
    expect(1 + 1).toBe(2);
  });
});
```

Run: `docker compose exec -T web npm test`
Expected: 1 passed.

- [ ] **Step 5: Delete the smoke test, commit setup**

```bash
rm web/src/lib/_vitest_smoke.test.ts
git add web/package.json web/package-lock.json web/vitest.config.ts
git commit -m "chore(web): add vitest for unit tests"
```

---

## Task 2 — `ACLDiffItem` type + diff entry point

Define the diff item discriminated union and the top-level `diffACL` shell that dispatches by `acl.type`.

**Files:**
- Create: `web/src/lib/aclDiff.ts`
- Create: `web/src/lib/aclDiff.test.ts`

- [ ] **Step 1: Write failing tests for the entry-point dispatcher**

Create `web/src/lib/aclDiff.test.ts`:

```ts
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
```

- [ ] **Step 2: Run tests — confirm they fail with "not exported"**

Run: `docker compose exec -T web npm test`
Expected: FAIL — `diffACL` is not exported.

- [ ] **Step 3: Implement the entry point**

Create `web/src/lib/aclDiff.ts`:

```ts
import type { ACL, ACLType } from "../types";

export type ACLDiffKind =
  | "type_changed"
  | "added"
  | "removed"
  | "modified"
  | "reordered"
  | "owner_changed"
  | "group_changed";

export type ACLDiffItem =
  | { kind: "type_changed"; from: ACLType | "none"; to: ACLType | "none" }
  | { kind: "added";        scope?: string; summary: string }
  | { kind: "removed";      scope?: string; summary: string }
  | { kind: "modified";     scope?: string; summary: string }
  | { kind: "reordered";    scope?: string; summary: string }
  | { kind: "owner_changed"; from: string; to: string }
  | { kind: "group_changed"; from: string; to: string };

export function diffACL(prev: ACL | null, curr: ACL | null): ACLDiffItem[] {
  if (prev === null && curr === null) return [];
  if (prev === null && curr !== null) return [{ kind: "type_changed", from: "none", to: curr.type }];
  if (prev !== null && curr === null) return [{ kind: "type_changed", from: prev.type, to: "none" }];
  if (prev!.type !== curr!.type) {
    return [{ kind: "type_changed", from: prev!.type, to: curr!.type }];
  }
  // Same type — strategy dispatch added in later tasks.
  return [];
}
```

- [ ] **Step 4: Run tests — confirm they pass**

Run: `docker compose exec -T web npm test`
Expected: 5 passed.

- [ ] **Step 5: Commit**

```bash
git add web/src/lib/aclDiff.ts web/src/lib/aclDiff.test.ts
git commit -m "feat(web): aclDiff entry point and type union"
```

---

## Task 3 — POSIX strategy

POSIX is keyed by `(tag, qualifier)`. Order doesn't matter. Runs once over `entries` and (if present) once over `default_entries` with a `[default]` scope label.

**Files:**
- Modify: `web/src/lib/aclDiff.ts`
- Modify: `web/src/lib/aclDiff.test.ts`

- [ ] **Step 1: Add failing tests for POSIX**

Append to `web/src/lib/aclDiff.test.ts`:

```ts
import type { PosixACL, PosixACE } from "../types";

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
```

- [ ] **Step 2: Run tests — confirm POSIX tests fail**

Run: `docker compose exec -T web npm test`
Expected: POSIX tests FAIL (dispatcher returns `[]` for same-type today).

- [ ] **Step 3: Implement the POSIX strategy**

In `web/src/lib/aclDiff.ts`, add (and wire into the dispatcher):

```ts
import type { PosixACE, PosixACL } from "../types";

function posixKey(ace: PosixACE): string {
  return ace.qualifier ? `${ace.tag}:${ace.qualifier}` : ace.tag;
}

function posixSummary(ace: PosixACE): string {
  return `${posixKey(ace)} ${ace.perms}`;
}

function diffPosixEntries(
  prev: PosixACE[],
  curr: PosixACE[],
  scope?: string,
): ACLDiffItem[] {
  const out: ACLDiffItem[] = [];
  const prevByKey = new Map(prev.map((a) => [posixKey(a), a]));
  const currByKey = new Map(curr.map((a) => [posixKey(a), a]));

  // Stable order: walk the union of keys in insertion order of prev then curr.
  const seen = new Set<string>();
  const orderedKeys = [
    ...prev.map(posixKey),
    ...curr.map(posixKey).filter((k) => !prevByKey.has(k)),
  ].filter((k) => (seen.has(k) ? false : (seen.add(k), true)));

  for (const k of orderedKeys) {
    const a = prevByKey.get(k);
    const b = currByKey.get(k);
    if (a && !b) out.push({ kind: "removed", ...(scope ? { scope } : {}), summary: posixSummary(a) });
    else if (!a && b) out.push({ kind: "added", ...(scope ? { scope } : {}), summary: posixSummary(b) });
    else if (a && b && a.perms !== b.perms) {
      out.push({
        kind: "modified",
        ...(scope ? { scope } : {}),
        summary: `${posixKey(a)} ${a.perms} → ${b.perms}`,
      });
    }
  }
  return out;
}

function diffPosix(prev: PosixACL, curr: PosixACL): ACLDiffItem[] {
  const access = diffPosixEntries(prev.entries, curr.entries);
  const prevDefault = prev.default_entries ?? [];
  const currDefault = curr.default_entries ?? [];
  const def = diffPosixEntries(prevDefault, currDefault, "default");
  return [...access, ...def];
}
```

Wire into the dispatcher (replace the `// Same type — …` block):

```ts
  if (prev!.type === "posix" && curr!.type === "posix") return diffPosix(prev as PosixACL, curr as PosixACL);
  return [];
```

- [ ] **Step 4: Run tests — confirm POSIX tests pass**

Run: `docker compose exec -T web npm test`
Expected: all POSIX tests PASS (5 in the POSIX block + 5 from Task 2 = 10 total).

- [ ] **Step 5: Commit**

```bash
git add web/src/lib/aclDiff.ts web/src/lib/aclDiff.test.ts
git commit -m "feat(web): POSIX ACL diff strategy with default-ACL scope"
```

---

## Task 4 — NFSv4 strategy (ordered with reorder reporting)

NFSv4 ACEs are evaluated in order, so reordering changes effective access. The strategy uses an LCS-based diff and emits a `reordered` item when the surviving (kept) ACEs change order between prev and curr.

**Files:**
- Modify: `web/src/lib/aclDiff.ts`
- Modify: `web/src/lib/aclDiff.test.ts`

- [ ] **Step 1: Add failing tests for NFSv4**

Append to `web/src/lib/aclDiff.test.ts`:

```ts
import type { NfsV4ACL, NfsV4ACE } from "../types";

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
```

- [ ] **Step 2: Run tests — confirm they fail**

Run: `docker compose exec -T web npm test`
Expected: NFSv4 tests FAIL.

- [ ] **Step 3: Implement NFSv4 strategy**

In `web/src/lib/aclDiff.ts`:

```ts
import type { NfsV4ACE, NfsV4ACL } from "../types";

function nfsKey(ace: NfsV4ACE): string {
  // (principal, ace_type) — semantic identity for "is this the same ACE".
  return `${ace.principal}\x00${ace.ace_type}`;
}

function nfsSummary(ace: NfsV4ACE): string {
  const flags = ace.flags.length ? ` [${ace.flags.join(",")}]` : "";
  return `${ace.principal} ${ace.ace_type} ${ace.mask.join(",")}${flags}`;
}

function arraysEqual(a: string[], b: string[]): boolean {
  if (a.length !== b.length) return false;
  for (let i = 0; i < a.length; i++) if (a[i] !== b[i]) return false;
  return true;
}

function nfsAcesEqual(a: NfsV4ACE, b: NfsV4ACE): boolean {
  return (
    a.principal === b.principal &&
    a.ace_type === b.ace_type &&
    arraysEqual(a.mask, b.mask) &&
    arraysEqual(a.flags, b.flags)
  );
}

function diffOrderedAces<T>(
  prev: T[],
  curr: T[],
  keyFn: (t: T) => string,
  eqFn: (a: T, b: T) => boolean,
  summaryFn: (t: T) => string,
  modifiedSummary: (a: T, b: T) => string,
  scope?: string,
): ACLDiffItem[] {
  const out: ACLDiffItem[] = [];

  const prevByKey = new Map(prev.map((a) => [keyFn(a), a]));
  const currByKey = new Map(curr.map((a) => [keyFn(a), a]));

  // Removed (in prev, not in curr).
  for (const a of prev) {
    if (!currByKey.has(keyFn(a))) {
      out.push({ kind: "removed", ...(scope ? { scope } : {}), summary: summaryFn(a) });
    }
  }
  // Added or modified (in curr).
  for (const b of curr) {
    const a = prevByKey.get(keyFn(b));
    if (!a) {
      out.push({ kind: "added", ...(scope ? { scope } : {}), summary: summaryFn(b) });
    } else if (!eqFn(a, b)) {
      out.push({ kind: "modified", ...(scope ? { scope } : {}), summary: modifiedSummary(a, b) });
    }
  }

  // Reorder detection: relative order of kept keys.
  const keptPrev = prev.map(keyFn).filter((k) => currByKey.has(k));
  const keptCurr = curr.map(keyFn).filter((k) => prevByKey.has(k));
  if (!arraysEqual(keptPrev, keptCurr)) {
    out.push({
      kind: "reordered",
      ...(scope ? { scope } : {}),
      summary: "ACE order changed (significant for evaluation)",
    });
  }
  return out;
}

function diffNfsV4(prev: NfsV4ACL, curr: NfsV4ACL): ACLDiffItem[] {
  return diffOrderedAces(
    prev.entries,
    curr.entries,
    nfsKey,
    nfsAcesEqual,
    nfsSummary,
    (a, b) => `${a.principal} ${a.ace_type} ${a.mask.join(",")} → ${b.mask.join(",")}`,
  );
}
```

Wire into dispatcher:

```ts
  if (prev!.type === "nfsv4" && curr!.type === "nfsv4") return diffNfsV4(prev as NfsV4ACL, curr as NfsV4ACL);
```

- [ ] **Step 4: Run tests — confirm they pass**

Run: `docker compose exec -T web npm test`
Expected: all tests PASS (Task 2: 5 + POSIX: 7 + NFSv4: 5 = 17 total).

- [ ] **Step 5: Commit**

```bash
git add web/src/lib/aclDiff.ts web/src/lib/aclDiff.test.ts
git commit -m "feat(web): NFSv4 ACL diff strategy with reorder detection"
```

---

## Task 5 — NT strategy (NFSv4-style + owner/group + inherited grouping)

NT ACL is ordered like NFSv4, with two additions: first-class `owner_changed` / `group_changed` items, and a separate "inherited" scope so the renderer can collapse those by default.

**Files:**
- Modify: `web/src/lib/aclDiff.ts`
- Modify: `web/src/lib/aclDiff.test.ts`

- [ ] **Step 1: Add failing tests for NT**

Append to `web/src/lib/aclDiff.test.ts`:

```ts
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
```

- [ ] **Step 2: Run tests — confirm they fail**

Run: `docker compose exec -T web npm test`
Expected: NT tests FAIL.

- [ ] **Step 3: Implement NT strategy**

In `web/src/lib/aclDiff.ts`:

```ts
import type { NtACE, NtACL, NtPrincipal } from "../types";

function ntKey(ace: NtACE): string {
  return `${ace.sid}\x00${ace.ace_type}`;
}

function ntSummary(ace: NtACE): string {
  const label = ace.name || ace.sid;
  const flags = ace.flags.filter((f) => f !== "inherited");
  const flagStr = flags.length ? ` [${flags.join(",")}]` : "";
  const inherited = ace.flags.includes("inherited") ? " [inherited]" : "";
  return `${label} ${ace.ace_type} ${ace.mask.join(",")}${flagStr}${inherited}`;
}

function ntAcesEqual(a: NtACE, b: NtACE): boolean {
  return (
    a.sid === b.sid &&
    a.ace_type === b.ace_type &&
    arraysEqual(a.mask, b.mask) &&
    arraysEqual(a.flags, b.flags)
  );
}

function principalLabel(p: NtPrincipal | null): string {
  if (!p) return "(none)";
  return p.name || p.sid;
}

function isInherited(ace: NtACE): boolean {
  return ace.flags.includes("inherited");
}

function diffNt(prev: NtACL, curr: NtACL): ACLDiffItem[] {
  const out: ACLDiffItem[] = [];

  // Owner / group first.
  const prevOwner = principalLabel(prev.owner);
  const currOwner = principalLabel(curr.owner);
  if (prevOwner !== currOwner) {
    out.push({ kind: "owner_changed", from: prevOwner, to: currOwner });
  }
  const prevGroup = principalLabel(prev.group);
  const currGroup = principalLabel(curr.group);
  if (prevGroup !== currGroup) {
    out.push({ kind: "group_changed", from: prevGroup, to: currGroup });
  }

  // Direct ACEs.
  const prevDirect = prev.entries.filter((a) => !isInherited(a));
  const currDirect = curr.entries.filter((a) => !isInherited(a));
  out.push(
    ...diffOrderedAces(
      prevDirect,
      currDirect,
      ntKey,
      ntAcesEqual,
      ntSummary,
      (a, b) => `${a.name || a.sid} ${a.ace_type} ${a.mask.join(",")} → ${b.mask.join(",")}`,
    ),
  );

  // Inherited ACEs (separate scope so the renderer can collapse).
  const prevInherited = prev.entries.filter(isInherited);
  const currInherited = curr.entries.filter(isInherited);
  out.push(
    ...diffOrderedAces(
      prevInherited,
      currInherited,
      ntKey,
      ntAcesEqual,
      ntSummary,
      (a, b) => `${a.name || a.sid} ${a.ace_type} ${a.mask.join(",")} → ${b.mask.join(",")}`,
      "inherited",
    ),
  );

  return out;
}
```

Wire into dispatcher:

```ts
  if (prev!.type === "nt" && curr!.type === "nt") return diffNt(prev as NtACL, curr as NtACL);
```

- [ ] **Step 4: Run tests — confirm they pass**

Run: `docker compose exec -T web npm test`
Expected: all tests PASS (Task 2: 5 + POSIX: 7 + NFSv4: 5 + NT: 6 = 23 total).

- [ ] **Step 5: Commit**

```bash
git add web/src/lib/aclDiff.ts web/src/lib/aclDiff.test.ts
git commit -m "feat(web): NT ACL diff strategy with owner/group and inherited scope"
```

---

## Task 6 — S3 strategy

S3 grants are keyed by `(grantee_type, grantee_id, permission)`. Order doesn't matter. Owner change is first-class.

**Files:**
- Modify: `web/src/lib/aclDiff.ts`
- Modify: `web/src/lib/aclDiff.test.ts`

- [ ] **Step 1: Add failing tests for S3**

Append to `web/src/lib/aclDiff.test.ts`:

```ts
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
```

- [ ] **Step 2: Run tests — confirm they fail**

Run: `docker compose exec -T web npm test`
Expected: S3 tests FAIL.

- [ ] **Step 3: Implement S3 strategy**

In `web/src/lib/aclDiff.ts`:

```ts
import type { S3ACL, S3Grant, S3Owner } from "../types";

function s3OwnerLabel(o: S3Owner | null): string {
  if (!o) return "(none)";
  return o.display_name || o.id;
}

function s3GrantKey(g: S3Grant): string {
  return `${g.grantee_type}\x00${g.grantee_id}\x00${g.permission}`;
}

function s3GrantSummary(g: S3Grant): string {
  // Use grantee_name if present, else grantee_id; for groups the id is meaningful.
  const label = g.grantee_name || g.grantee_id;
  return `${g.grantee_type === "canonical_user" ? "user" : g.grantee_type}:${label} ${g.permission}`;
}

function diffS3(prev: S3ACL, curr: S3ACL): ACLDiffItem[] {
  const out: ACLDiffItem[] = [];

  const prevOwner = s3OwnerLabel(prev.owner);
  const currOwner = s3OwnerLabel(curr.owner);
  if (prevOwner !== currOwner) {
    out.push({ kind: "owner_changed", from: prevOwner, to: currOwner });
  }

  const prevByKey = new Map(prev.grants.map((g) => [s3GrantKey(g), g]));
  const currByKey = new Map(curr.grants.map((g) => [s3GrantKey(g), g]));

  for (const g of prev.grants) {
    if (!currByKey.has(s3GrantKey(g))) out.push({ kind: "removed", summary: s3GrantSummary(g) });
  }
  for (const g of curr.grants) {
    if (!prevByKey.has(s3GrantKey(g))) out.push({ kind: "added", summary: s3GrantSummary(g) });
  }

  return out;
}
```

Wire into dispatcher:

```ts
  if (prev!.type === "s3" && curr!.type === "s3") return diffS3(prev as S3ACL, curr as S3ACL);
```

- [ ] **Step 4: Run tests — confirm they pass**

Run: `docker compose exec -T web npm test`
Expected: all tests PASS (Task 2: 5 + POSIX: 7 + NFSv4: 5 + NT: 6 + S3: 5 = 28 total).

- [ ] **Step 5: Commit**

```bash
git add web/src/lib/aclDiff.ts web/src/lib/aclDiff.test.ts
git commit -m "feat(web): S3 ACL diff strategy"
```

---

## Task 7 — `<ACLDiff>` React component

Renders an `ACLDiffItem[]` as a small list with per-kind icons and colors. Inherited NT items are collapsed behind a toggle.

**Files:**
- Create: `web/src/components/acl/ACLDiff.tsx`

- [ ] **Step 1: Implement the component**

Create `web/src/components/acl/ACLDiff.tsx`:

```tsx
import { useState } from "react";
import type { ACL } from "../../types";
import { diffACL, type ACLDiffItem } from "../../lib/aclDiff";

const ICON: Record<ACLDiffItem["kind"], string> = {
  type_changed:  "⇄",
  added:         "+",
  removed:       "−",
  modified:      "~",
  reordered:     "↕",
  owner_changed: "↻",
  group_changed: "↻",
};

const COLOR: Record<ACLDiffItem["kind"], string> = {
  type_changed:  "text-gray-600",
  added:         "text-emerald-700",
  removed:       "text-red-700",
  modified:      "text-amber-700",
  reordered:     "text-violet-700",
  owner_changed: "text-blue-700",
  group_changed: "text-blue-700",
};

function itemText(item: ACLDiffItem): string {
  switch (item.kind) {
    case "type_changed":  return `Type ${item.from} → ${item.to}`;
    case "owner_changed": return `Owner ${item.from} → ${item.to}`;
    case "group_changed": return `Group ${item.from} → ${item.to}`;
    case "added":
    case "removed":
    case "modified":
    case "reordered":
      return item.summary;
  }
}

function DiffRow({ item }: { item: ACLDiffItem }) {
  const scope = "scope" in item && item.scope ? `[${item.scope}] ` : "";
  return (
    <li className={`text-sm ${COLOR[item.kind]} flex items-baseline gap-2`}>
      <span className="font-mono w-3 text-center">{ICON[item.kind]}</span>
      <span>
        {scope}
        {itemText(item)}
      </span>
    </li>
  );
}

export function ACLDiff({ prev, curr }: { prev: ACL | null; curr: ACL | null }) {
  const items = diffACL(prev, curr);
  const [showInherited, setShowInherited] = useState(false);

  if (items.length === 0) return null;

  const direct = items.filter((i) => !("scope" in i && i.scope === "inherited"));
  const inherited = items.filter((i) => "scope" in i && i.scope === "inherited");

  return (
    <div className="mt-1">
      <ul className="space-y-0.5">
        {direct.map((item, i) => (
          <DiffRow key={i} item={item} />
        ))}
      </ul>
      {inherited.length > 0 && (
        <div className="mt-1">
          <button
            type="button"
            onClick={() => setShowInherited((v) => !v)}
            className="text-xs text-gray-500 hover:text-gray-700"
          >
            {showInherited ? "▾" : "▸"} Inherited changes ({inherited.length})
          </button>
          {showInherited && (
            <ul className="space-y-0.5 mt-1 ml-3">
              {inherited.map((item, i) => (
                <DiffRow key={i} item={item} />
              ))}
            </ul>
          )}
        </div>
      )}
    </div>
  );
}
```

- [ ] **Step 2: Verify TypeScript compiles**

Run: `docker compose exec -T web npx tsc --noEmit`
Expected: no errors.

- [ ] **Step 3: Commit**

```bash
git add web/src/components/acl/ACLDiff.tsx
git commit -m "feat(web): ACLDiff component renders per-type diff items"
```

---

## Task 8 — Wire `<ACLDiff>` into `EntryDetail`

Replace the inline `if (JSON.stringify(v.acl …)) changes.push("acl")` branch with the new component. All other change labels (mode/ownership/content/etc.) stay exactly as today.

**Files:**
- Modify: `web/src/components/EntryDetail.tsx`

- [ ] **Step 1: Edit `EntryDetail.tsx`**

In the `Version history` `<Section>` block, replace the existing `versions.map` body so the ACL change becomes a sub-element of the list item rather than a string entry in `changes`:

Old (lines 184-220 region):

```tsx
{entry.versions.map((v, i) => {
  const prev = entry.versions[i + 1];
  const changes: string[] = [];
  if (prev) {
    if (v.content_hash !== prev.content_hash) changes.push("content");
    if (v.size_bytes !== prev.size_bytes) changes.push("size");
    if (v.mode !== prev.mode) changes.push("mode");
    if (v.uid !== prev.uid || v.gid !== prev.gid) changes.push("ownership");
    if (
      JSON.stringify(v.acl ?? null) !==
      JSON.stringify(prev.acl ?? null)
    )
      changes.push("acl");
    if (
      JSON.stringify(v.xattrs ?? null) !==
      JSON.stringify(prev.xattrs ?? null)
    )
      changes.push("xattrs");
  }
  return (
    <li
      key={v.id}
      className="border-l-2 border-accent-200 pl-3 py-0.5"
    >
      <div className="text-xs text-gray-500">
        {formatDateTime(v.detected_at)}
      </div>
      <div className="text-sm text-gray-800 mt-0.5">
        {prev
          ? changes.length > 0
            ? `Changed: ${changes.join(", ")}`
            : "Re-observed (no field changed)"
          : "First observation"}
      </div>
    </li>
  );
})}
```

New:

```tsx
{entry.versions.map((v, i) => {
  const prev = entry.versions[i + 1];
  const nonAclChanges: string[] = [];
  let aclChanged = false;
  if (prev) {
    if (v.content_hash !== prev.content_hash) nonAclChanges.push("content");
    if (v.size_bytes !== prev.size_bytes) nonAclChanges.push("size");
    if (v.mode !== prev.mode) nonAclChanges.push("mode");
    if (v.uid !== prev.uid || v.gid !== prev.gid) nonAclChanges.push("ownership");
    if (
      JSON.stringify(v.xattrs ?? null) !==
      JSON.stringify(prev.xattrs ?? null)
    )
      nonAclChanges.push("xattrs");
    if (
      JSON.stringify(v.acl ?? null) !==
      JSON.stringify(prev.acl ?? null)
    )
      aclChanged = true;
  }
  const label = !prev
    ? "First observation"
    : nonAclChanges.length === 0 && !aclChanged
      ? "Re-observed (no field changed)"
      : nonAclChanges.length > 0
        ? `Changed: ${nonAclChanges.join(", ")}`
        : null;
  return (
    <li
      key={v.id}
      className="border-l-2 border-accent-200 pl-3 py-0.5"
    >
      <div className="text-xs text-gray-500">
        {formatDateTime(v.detected_at)}
      </div>
      {label && (
        <div className="text-sm text-gray-800 mt-0.5">{label}</div>
      )}
      {aclChanged && prev && (
        <ACLDiff prev={prev.acl} curr={v.acl} />
      )}
    </li>
  );
})}
```

- [ ] **Step 2: Add the import**

At the top of `EntryDetail.tsx`, add:

```tsx
import { ACLDiff } from "./acl/ACLDiff";
```

- [ ] **Step 3: Verify TypeScript compiles**

Run: `docker compose exec -T web npx tsc --noEmit`
Expected: no errors.

- [ ] **Step 4: Verify Vite build still succeeds**

Run: `docker compose exec -T web npm run build`
Expected: build completes without error.

- [ ] **Step 5: Commit**

```bash
git add web/src/components/EntryDetail.tsx
git commit -m "feat(web): wire ACLDiff into EntryDetail version history"
```

---

## Task 9 — End-to-end manual verification

The pure functions are unit-tested; the wiring needs an eyes-on check against real entries.

**Files:** none.

- [ ] **Step 1: Bring the stack up**

Run:

```bash
docker compose up -d
```

- [ ] **Step 2: Create an entry, scan, change ACL, scan again**

Use a local source pointing at a temp dir:

```bash
mkdir -p /tmp/acl-diff-demo
echo "before" > /tmp/acl-diff-demo/file.txt
chmod 644 /tmp/acl-diff-demo/file.txt
setfacl -m u:nobody:r-- /tmp/acl-diff-demo/file.txt
```

Trigger a scan via the API (substitute `$SID`/`$T`):

```bash
curl -s -X POST -H "Authorization: Bearer $T" "http://127.0.0.1:8000/api/sources/$SID/scan" | jq
```

Wait for completion (poll `GET /api/sources/$SID/scans?limit=1`).

Now change the ACL:

```bash
setfacl -m u:nobody:rwx /tmp/acl-diff-demo/file.txt
setfacl -m u:bin:r-- /tmp/acl-diff-demo/file.txt
```

Trigger a second scan and wait.

- [ ] **Step 3: Verify in the UI**

Open `http://127.0.0.1:5173/browse`, navigate to `/tmp/acl-diff-demo/file.txt`, open the drawer.

Expected in **Version history** for the latest version:

```
+ user:bin r--
~ user:nobody r-- → rwx
```

Older entries still show "First observation" or `Changed: …` for non-ACL fields.

- [ ] **Step 4: Sanity-check non-ACL changes still work**

```bash
chmod 600 /tmp/acl-diff-demo/file.txt
echo "after" > /tmp/acl-diff-demo/file.txt
```

Scan again. Drawer should show `Changed: content, size, mode` on the new version (no ACL diff lines, since ACL didn't change).

- [ ] **Step 5: Tear down demo**

```bash
rm -rf /tmp/acl-diff-demo
```

No commit — verification only.

---

## Notes for the implementer

- **Don't restructure the version-history rendering beyond what Task 8 shows.** Other phases will likely add per-field formatters (e.g., octal/symbolic mode shown in the diff), but that's out of scope here.
- **Keep `aclDiff.ts` pure.** No React imports, no side effects, no I/O. The test file proves this — if you accidentally pull a React import, the node-environment vitest run will surface it loud.
- **Don't worry about i18n or rich pluralization.** The strings are short, English-only, internal-tool fare.
- **Tasks 3-6 grow the same `aclDiff.ts` module** — you can safely re-arrange helpers (`arraysEqual`, etc.) into one section, but keep each strategy function self-contained and the dispatcher tidy.
- **No backend touches.** No Pydantic, no SQLAlchemy, no Go.
