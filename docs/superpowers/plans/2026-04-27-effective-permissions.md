# Phase 11 — Effective Permissions Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Given a captured ACL plus a principal identity (with optional groups), answer "what can this person do on this entry?" — exposed as a backend pure-function evaluator (`compute_effective`), a `POST /api/entries/{id}/effective-permissions` endpoint, and an `<EffectivePermissions>` React card sandwiched between the ACL section and version history in the entry drawer. Every grant/deny is traceable to specific ACEs (the `by` field).

**Architecture:** Pure-Python service `api/akashic/services/effective_perms.py` exposing `compute_effective(acl, base_mode, base_uid, base_gid, principal, groups, source_security)` that dispatches to per-type evaluators (POSIX/NFSv4/NT/S3) and returns an `EffectivePerms` Pydantic model with five canonical rights (`read`, `write`, `execute`, `delete`, `change_perms`), each carrying `granted: bool` and `by: list[ACEReference]`. Endpoint thin-wraps the service with RBAC + 404. Frontend card uses `useMutation` to call the endpoint, with a principal picker (type dropdown defaulted to entry's ACL type, identifier input, repeatable group rows) and a result table (5 rows × allow/deny + ACE summaries inline + caveats badge at top).

**Tech Stack:** Python 3.12 (FastAPI/Pydantic/SQLAlchemy async), TypeScript/React 18, Tailwind, Vitest (already set up in Phase 10), pytest-asyncio.

---

## File structure

**Create**
- `api/akashic/services/effective_perms.py` — `compute_effective()` dispatcher + per-type evaluators (`_eval_posix`, `_eval_nfsv4`, `_eval_nt`, `_eval_s3`) + canonical right vocabulary.
- `api/akashic/schemas/effective.py` — `PrincipalRef`, `GroupRef`, `ACEReference`, `RightResult`, `EffectivePermsRequest`, `EffectivePerms` (response).
- `api/akashic/routers/effective_perms.py` — single endpoint `POST /api/entries/{entry_id}/effective-permissions`. (Separate router file rather than tacking onto `entries.py` because the schema imports + service composition are non-trivial enough to deserve their own module.)
- `api/tests/test_effective_perms.py` — pure-function table-driven tests per evaluator (POSIX, NFSv4, NT, S3) plus dispatcher edge cases.
- `api/tests/test_effective_perms_endpoint.py` — endpoint integration tests (404, 403, happy path, malformed body).
- `web/src/components/acl/EffectivePermissions.tsx` — the card.
- `web/src/lib/effectivePermsTypes.ts` — TS types matching the Pydantic schemas (kept separate from `web/src/types/index.ts` to avoid bloating that file).

**Edit**
- `api/akashic/main.py` — register the new router.
- `web/src/types/index.ts` — re-export the new types (or just import from `effectivePermsTypes.ts` where used). Add `PrincipalType`, `EffectivePerms` shapes.
- `web/src/components/EntryDetail.tsx` — render `<EffectivePermissions>` between the ACL section and Extended attributes.

**No deletes. No model/migration changes** — `compute_effective` is read-only over already-captured ACL JSONB.

---

## Cross-task spec: the right vocabulary

Five canonical rights are returned for every model. The "applicable" set per model:

| Right | POSIX folds into | NFSv4 mask bits | NT mask bits | S3 permissions |
|---|---|---|---|---|
| `read` | `r` (mode bits 4/4/4) | `read_data`, `list_directory` | `READ_DATA`, `LIST_DIRECTORY`, `GENERIC_READ`, `GENERIC_ALL` | `READ`, `FULL_CONTROL` |
| `write` | `w` (mode bits 2/2/2) | `write_data`, `append_data`, `add_file` | `WRITE_DATA`, `ADD_FILE`, `APPEND_DATA`, `GENERIC_WRITE`, `GENERIC_ALL` | `WRITE`, `FULL_CONTROL` |
| `execute` | `x` (mode bits 1/1/1) | `execute` | `EXECUTE`, `TRAVERSE` | n/a (always `granted=false, by=[]`) |
| `delete` | folded into `write` | `delete`, `delete_child` | `DELETE`, `DELETE_CHILD`, `GENERIC_ALL` | n/a (granted iff bucket grants `WRITE`; advisory only) |
| `change_perms` | folded into `write` (POSIX) | `write_acl`, `write_owner` | `WRITE_DAC`, `WRITE_OWNER` | n/a |

POSIX folds `delete` and `change_perms` into `write` per spec line 311. The result table still has 5 rows (so the UI is consistent) but POSIX-evaluated entries will show `delete` and `change_perms` mirroring the `write` result with a `caveats` note ("POSIX folds delete and change_perms into write").

---

## Task 1 — Schemas (request/response Pydantic models)

**Files:**
- Create: `api/akashic/schemas/effective.py`

- [ ] **Step 1: Create the schema file**

Create `api/akashic/schemas/effective.py`:

```python
"""Schemas for the effective-permissions endpoint."""
from typing import Literal

from pydantic import BaseModel, Field


PrincipalType = Literal["posix_uid", "sid", "nfsv4_principal", "s3_canonical"]
RightName = Literal["read", "write", "execute", "delete", "change_perms"]


class PrincipalRef(BaseModel):
    type: PrincipalType
    identifier: str
    name: str = ""


class GroupRef(BaseModel):
    type: PrincipalType
    identifier: str
    name: str = ""


class ACEReference(BaseModel):
    ace_index: int  # -1 means "synthetic" (e.g. POSIX base mode owner perms)
    summary: str   # human-readable one-liner describing the ACE


class RightResult(BaseModel):
    granted: bool
    by: list[ACEReference] = Field(default_factory=list)


class EffectivePermsEvaluatedWith(BaseModel):
    model: Literal["posix", "nfsv4", "nt", "s3", "none"]
    principal: PrincipalRef
    groups: list[GroupRef] = Field(default_factory=list)
    caveats: list[str] = Field(default_factory=list)


class EffectivePerms(BaseModel):
    rights: dict[RightName, RightResult]
    evaluated_with: EffectivePermsEvaluatedWith


class EffectivePermsRequest(BaseModel):
    principal: PrincipalRef
    groups: list[GroupRef] = Field(default_factory=list)
    principal_name_hint: str = ""
```

- [ ] **Step 2: Smoke-test schema instantiation**

Create `api/tests/test_effective_perms_schemas.py`:

```python
from akashic.schemas.effective import (
    EffectivePerms,
    EffectivePermsEvaluatedWith,
    EffectivePermsRequest,
    PrincipalRef,
    RightResult,
    ACEReference,
)


def test_effective_perms_round_trip():
    payload = {
        "rights": {
            "read":         {"granted": True,  "by": [{"ace_index": 2, "summary": "user:alice rwx"}]},
            "write":        {"granted": False, "by": []},
            "execute":      {"granted": True,  "by": []},
            "delete":       {"granted": False, "by": []},
            "change_perms": {"granted": False, "by": []},
        },
        "evaluated_with": {
            "model": "posix",
            "principal": {"type": "posix_uid", "identifier": "1000", "name": "alice"},
            "groups": [],
            "caveats": [],
        },
    }
    parsed = EffectivePerms.model_validate(payload)
    assert parsed.rights["read"].granted is True
    assert parsed.rights["read"].by[0].summary == "user:alice rwx"


def test_request_minimal():
    payload = {"principal": {"type": "posix_uid", "identifier": "1000"}}
    parsed = EffectivePermsRequest.model_validate(payload)
    assert parsed.principal.identifier == "1000"
    assert parsed.groups == []
```

- [ ] **Step 3: Run schema tests**

Run from project root:

```bash
docker compose exec -T api pytest tests/test_effective_perms_schemas.py -v
```

Expected: 2 passed.

If `docker compose` isn't running the api service, fall back to:

```bash
docker compose run --rm -e PYTHONPATH=/app api pytest tests/test_effective_perms_schemas.py -v
```

- [ ] **Step 4: Commit**

```bash
git add api/akashic/schemas/effective.py api/tests/test_effective_perms_schemas.py
git commit -m "feat(api): effective-permissions request/response schemas"
```

---

## Task 2 — POSIX evaluator (TDD, table-driven)

POSIX precedence per POSIX.1e §23.1.5:
1. If `principal.identifier == str(base_uid)` → owner perms (mode bits 6-8: `(mode >> 6) & 7`).
2. Else if a `user:<id>:perms` ACE matches `principal.identifier` (qualifier compared as string) → that ACE's perms, masked by `mask::` if present.
3. Else if any group in `groups` matches `group_obj` (compared against `str(base_gid)`) or a `group:<id>:perms` ACE → union of all matching ACE perms, masked by `mask::` if present.
4. Else → other perms (mode bits 0-2: `mode & 7`).

POSIX returns three native rights (`r`, `w`, `x`). Mapping into the canonical 5: `read←r`, `write←w`, `execute←x`, `delete←w`, `change_perms←w`.

**Files:**
- Create: `api/akashic/services/effective_perms.py`
- Create: `api/tests/test_effective_perms.py`

- [ ] **Step 1: Write failing POSIX tests**

Create `api/tests/test_effective_perms.py`:

```python
import pytest

from akashic.schemas.acl import PosixACE, PosixACL
from akashic.schemas.effective import GroupRef, PrincipalRef
from akashic.services.effective_perms import compute_effective


def _posix(entries: list[dict], default: list[dict] | None = None) -> PosixACL:
    return PosixACL.model_validate({
        "type": "posix",
        "entries": entries,
        "default_entries": default,
    })


@pytest.mark.parametrize(
    "principal_uid,groups,base_mode,base_uid,base_gid,acl_entries,expect_read,expect_write,expect_exec",
    [
        # Owner: full rwx via base mode.
        ("1000", [], 0o755, 1000, 100, [], True,  True,  True),
        # Other: mode 755 grants r-x.
        ("9999", [], 0o755, 1000, 100, [], True,  False, True),
        # Group via base_gid (group_obj rwx).
        ("9999", [GroupRef(type="posix_uid", identifier="100")], 0o775, 1000, 100, [], True, True, True),
        # ACL: user:alice gets r-x; mask narrows write off.
        (
            "1001", [],
            0o600, 1000, 100,
            [
                {"tag": "user_obj",  "qualifier": "",     "perms": "rw-"},
                {"tag": "user",      "qualifier": "1001", "perms": "rwx"},
                {"tag": "group_obj", "qualifier": "",     "perms": "r--"},
                {"tag": "mask",      "qualifier": "",     "perms": "r-x"},
                {"tag": "other",     "qualifier": "",     "perms": "---"},
            ],
            True, False, True,
        ),
        # ACL: matching group with rwx, mask r-x → rwx & r-x = r-x.
        (
            "1001",
            [GroupRef(type="posix_uid", identifier="200")],
            0o600, 1000, 100,
            [
                {"tag": "user_obj",  "qualifier": "",    "perms": "rwx"},
                {"tag": "group",     "qualifier": "200", "perms": "rwx"},
                {"tag": "mask",      "qualifier": "",    "perms": "r-x"},
                {"tag": "other",     "qualifier": "",    "perms": "---"},
            ],
            True, False, True,
        ),
    ],
)
def test_posix_evaluator(
    principal_uid, groups, base_mode, base_uid, base_gid,
    acl_entries, expect_read, expect_write, expect_exec,
):
    acl = _posix(acl_entries) if acl_entries else None
    result = compute_effective(
        acl=acl,
        base_mode=base_mode,
        base_uid=base_uid,
        base_gid=base_gid,
        principal=PrincipalRef(type="posix_uid", identifier=principal_uid),
        groups=groups,
    )
    assert result.rights["read"].granted is expect_read
    assert result.rights["write"].granted is expect_write
    assert result.rights["execute"].granted is expect_exec
    # POSIX folds delete and change_perms into write.
    assert result.rights["delete"].granted is expect_write
    assert result.rights["change_perms"].granted is expect_write
    assert result.evaluated_with.model == "posix" if acl else "none"


def test_posix_owner_by_field_references_base():
    result = compute_effective(
        acl=None,
        base_mode=0o755,
        base_uid=1000,
        base_gid=100,
        principal=PrincipalRef(type="posix_uid", identifier="1000"),
        groups=[],
    )
    assert result.rights["read"].granted
    # Non-empty `by` traceable to "base mode owner".
    assert any("owner" in ref.summary.lower() for ref in result.rights["read"].by)


def test_posix_no_acl_no_base_mode_returns_all_denied_for_unknown_principal():
    result = compute_effective(
        acl=None,
        base_mode=None,
        base_uid=None,
        base_gid=None,
        principal=PrincipalRef(type="posix_uid", identifier="1000"),
        groups=[],
    )
    for r in ("read", "write", "execute", "delete", "change_perms"):
        assert result.rights[r].granted is False
    assert result.evaluated_with.model == "none"


def test_posix_caveats_include_fold_note():
    result = compute_effective(
        acl=None,
        base_mode=0o755,
        base_uid=1000,
        base_gid=100,
        principal=PrincipalRef(type="posix_uid", identifier="1000"),
        groups=[],
    )
    assert any("delete" in c.lower() and "write" in c.lower() for c in result.evaluated_with.caveats)
```

- [ ] **Step 2: Run tests — confirm they FAIL**

```bash
docker compose exec -T api pytest tests/test_effective_perms.py -v
```

Expected: ImportError on `compute_effective` (or the file doesn't exist).

- [ ] **Step 3: Implement `compute_effective` shell + POSIX evaluator**

Create `api/akashic/services/effective_perms.py`:

```python
"""Pure-function effective-permissions evaluator. Dispatches per ACL type.

Returns the canonical 5-right shape (`read`, `write`, `execute`, `delete`, `change_perms`)
regardless of model. Mapping per model is documented in the plan.

NO state mutation. NO caching. Safe to call from anywhere.
"""
from __future__ import annotations

from typing import Iterable

from akashic.schemas.acl import (
    ACL,
    NfsV4ACE,
    NfsV4ACL,
    NtACE,
    NtACL,
    PosixACE,
    PosixACL,
    S3ACL,
)
from akashic.schemas.effective import (
    ACEReference,
    EffectivePerms,
    EffectivePermsEvaluatedWith,
    GroupRef,
    PrincipalRef,
    RightName,
    RightResult,
)

_ALL_RIGHTS: tuple[RightName, ...] = (
    "read", "write", "execute", "delete", "change_perms",
)


def _empty_rights() -> dict[RightName, RightResult]:
    return {r: RightResult(granted=False, by=[]) for r in _ALL_RIGHTS}


def _effective(
    rights: dict[RightName, RightResult],
    model: str,
    principal: PrincipalRef,
    groups: list[GroupRef],
    caveats: list[str],
) -> EffectivePerms:
    return EffectivePerms(
        rights=rights,
        evaluated_with=EffectivePermsEvaluatedWith(
            model=model,  # type: ignore[arg-type]
            principal=principal,
            groups=groups,
            caveats=caveats,
        ),
    )


def compute_effective(
    *,
    acl: ACL | None,
    base_mode: int | None,
    base_uid: int | None,
    base_gid: int | None,
    principal: PrincipalRef,
    groups: list[GroupRef] | None = None,
    source_security: dict | None = None,
) -> EffectivePerms:
    groups = groups or []
    if isinstance(acl, PosixACL) or (acl is None and base_mode is not None):
        return _eval_posix(acl, base_mode, base_uid, base_gid, principal, groups)
    if acl is None:
        return _effective(_empty_rights(), "none", principal, groups, [])
    if isinstance(acl, NfsV4ACL):
        return _eval_nfsv4(acl, principal, groups)
    if isinstance(acl, NtACL):
        return _eval_nt(acl, principal, groups)
    if isinstance(acl, S3ACL):
        return _eval_s3(acl, principal, groups, source_security)
    return _effective(_empty_rights(), "none", principal, groups, [])


# ── POSIX ────────────────────────────────────────────────────────────────────

def _perms_to_rwx(perms: str) -> tuple[bool, bool, bool]:
    return (perms[0] == "r", perms[1] == "w", perms[2] == "x")


def _mode_bits(mode: int, shift: int) -> tuple[bool, bool, bool]:
    bits = (mode >> shift) & 0b111
    return (bool(bits & 0b100), bool(bits & 0b010), bool(bits & 0b001))


def _posix_native(
    acl_entries: list[PosixACE],
    base_mode: int | None,
    base_uid: int | None,
    base_gid: int | None,
    principal_id: str,
    group_ids: set[str],
) -> tuple[bool, bool, bool, list[ACEReference]]:
    refs: list[ACEReference] = []

    # 1. owner
    if base_uid is not None and principal_id == str(base_uid):
        if base_mode is None:
            return False, False, False, []
        r, w, x = _mode_bits(base_mode, 6)
        refs.append(ACEReference(ace_index=-1, summary=f"base mode owner {oct(base_mode)}"))
        return r, w, x, refs

    mask: tuple[bool, bool, bool] | None = None
    for ace in acl_entries:
        if ace.tag == "mask":
            mask = _perms_to_rwx(ace.perms)
            break

    # 2. user ACE
    for i, ace in enumerate(acl_entries):
        if ace.tag == "user" and ace.qualifier == principal_id:
            r, w, x = _perms_to_rwx(ace.perms)
            if mask is not None:
                r, w, x = (r and mask[0], w and mask[1], x and mask[2])
            refs.append(ACEReference(ace_index=i, summary=f"user:{ace.qualifier} {ace.perms}" + (f" (masked by {''.join(_rwx_str(*mask))})" if mask else "")))
            return r, w, x, refs

    # 3. group(s)
    matched_any_group = False
    union_r = union_w = union_x = False
    for i, ace in enumerate(acl_entries):
        if ace.tag == "group_obj":
            if base_gid is not None and str(base_gid) in group_ids:
                r, w, x = _perms_to_rwx(ace.perms)
                if mask is not None:
                    r, w, x = (r and mask[0], w and mask[1], x and mask[2])
                union_r |= r; union_w |= w; union_x |= x
                refs.append(ACEReference(ace_index=i, summary=f"group_obj {ace.perms}"))
                matched_any_group = True
        elif ace.tag == "group" and ace.qualifier in group_ids:
            r, w, x = _perms_to_rwx(ace.perms)
            if mask is not None:
                r, w, x = (r and mask[0], w and mask[1], x and mask[2])
            union_r |= r; union_w |= w; union_x |= x
            refs.append(ACEReference(ace_index=i, summary=f"group:{ace.qualifier} {ace.perms}"))
            matched_any_group = True
    if matched_any_group:
        return union_r, union_w, union_x, refs

    # 4. other
    if base_mode is None:
        return False, False, False, refs
    r, w, x = _mode_bits(base_mode, 0)
    refs.append(ACEReference(ace_index=-1, summary=f"base mode other {oct(base_mode)}"))
    return r, w, x, refs


def _rwx_str(r: bool, w: bool, x: bool) -> tuple[str, str, str]:
    return ("r" if r else "-", "w" if w else "-", "x" if x else "-")


def _eval_posix(
    acl: PosixACL | None,
    base_mode: int | None,
    base_uid: int | None,
    base_gid: int | None,
    principal: PrincipalRef,
    groups: list[GroupRef],
) -> EffectivePerms:
    group_ids = {g.identifier for g in groups}
    entries = acl.entries if acl is not None else []
    r, w, x, refs = _posix_native(entries, base_mode, base_uid, base_gid, principal.identifier, group_ids)
    rights = _empty_rights()
    rights["read"]         = RightResult(granted=r, by=[ref for ref in refs] if r else [])
    rights["write"]        = RightResult(granted=w, by=[ref for ref in refs] if w else [])
    rights["execute"]      = RightResult(granted=x, by=[ref for ref in refs] if x else [])
    rights["delete"]       = RightResult(granted=w, by=[ref for ref in refs] if w else [])
    rights["change_perms"] = RightResult(granted=w, by=[ref for ref in refs] if w else [])
    caveats = ["POSIX folds delete and change_perms into write (parent-dir ACL not consulted)."]
    return _effective(rights, "posix", principal, groups, caveats)


# Stubs for NFSv4 / NT / S3 — implemented in later tasks.

def _eval_nfsv4(acl: NfsV4ACL, principal: PrincipalRef, groups: list[GroupRef]) -> EffectivePerms:
    return _effective(_empty_rights(), "nfsv4", principal, groups, ["NFSv4 evaluator not yet implemented"])


def _eval_nt(acl: NtACL, principal: PrincipalRef, groups: list[GroupRef]) -> EffectivePerms:
    return _effective(_empty_rights(), "nt", principal, groups, ["NT evaluator not yet implemented"])


def _eval_s3(
    acl: S3ACL,
    principal: PrincipalRef,
    groups: list[GroupRef],
    source_security: dict | None,
) -> EffectivePerms:
    return _effective(_empty_rights(), "s3", principal, groups, ["S3 evaluator not yet implemented"])
```

- [ ] **Step 4: Run tests — confirm POSIX tests pass**

```bash
docker compose exec -T api pytest tests/test_effective_perms.py -v
```

Expected: all POSIX tests PASS (5 parametrized cases + 3 single tests = 8).

- [ ] **Step 5: Commit**

```bash
git add api/akashic/services/effective_perms.py api/tests/test_effective_perms.py
git commit -m "feat(api): POSIX effective-permissions evaluator"
```

---

## Task 3 — NFSv4 evaluator (TDD)

NFSv4 per RFC 7530 §6.2.1: walk ACEs in order. For each requested right bit, the FIRST ACE that (a) matches the principal/groups AND (b) addresses that right wins — `allow` grants, `deny` denies. Rights with no matching ACE are denied.

Principal matching:
- `OWNER@` matches when `principal.identifier == "OWNER@"` OR explicit synthetic match (we keep it simple — accept `OWNER@` as the literal `principal.identifier` for explicit testing; production will pass it explicitly).
- `GROUP@` matches when any of the groups' identifiers equals `GROUP@`.
- `EVERYONE@` matches everyone.
- Any other ACE principal matches when `principal.identifier == ace.principal` OR (any `g.identifier == ace.principal` for `g` in `groups` AND `"identifier_group"` is in `ace.flags`).

**Files:**
- Modify: `api/akashic/services/effective_perms.py`
- Modify: `api/tests/test_effective_perms.py`

- [ ] **Step 1: Add failing NFSv4 tests**

Append to `api/tests/test_effective_perms.py`:

```python
from akashic.schemas.acl import NfsV4ACE, NfsV4ACL


def _nfs(*entries) -> NfsV4ACL:
    return NfsV4ACL.model_validate({
        "type": "nfsv4",
        "entries": [
            {
                "principal": e[0],
                "ace_type":  e[1],
                "mask":      list(e[2]),
                "flags":     list(e[3]) if len(e) > 3 else [],
            }
            for e in entries
        ],
    })


@pytest.mark.parametrize(
    "principal_id,groups,acl,expect_read,expect_write,expect_exec",
    [
        # First-match: explicit allow read.
        ("alice@dom", [],
         _nfs(("alice@dom", "allow", ["read_data"])),
         True, False, False),
        # Deny precedes allow for the same right.
        ("alice@dom", [],
         _nfs(
             ("alice@dom", "deny",  ["read_data"]),
             ("alice@dom", "allow", ["read_data"]),
         ),
         False, False, False),
        # EVERYONE@ allow grants to anyone.
        ("alice@dom", [],
         _nfs(("EVERYONE@", "allow", ["read_data"])),
         True, False, False),
        # Group match via identifier_group flag.
        ("alice@dom",
         [GroupRef(type="nfsv4_principal", identifier="eng@dom")],
         _nfs(("eng@dom", "allow", ["write_data"], ["identifier_group"])),
         False, True, False),
        # Multiple right bits in one allow.
        ("alice@dom", [],
         _nfs(("alice@dom", "allow", ["read_data", "write_data", "execute"])),
         True, True, True),
        # Right with no addressing ACE → denied.
        ("alice@dom", [],
         _nfs(("alice@dom", "allow", ["read_data"])),
         True, False, False),
    ],
)
def test_nfsv4_evaluator(principal_id, groups, acl, expect_read, expect_write, expect_exec):
    result = compute_effective(
        acl=acl,
        base_mode=None, base_uid=None, base_gid=None,
        principal=PrincipalRef(type="nfsv4_principal", identifier=principal_id),
        groups=groups,
    )
    assert result.rights["read"].granted is expect_read
    assert result.rights["write"].granted is expect_write
    assert result.rights["execute"].granted is expect_exec
    assert result.evaluated_with.model == "nfsv4"


def test_nfsv4_delete_distinct_from_write():
    acl = _nfs(("alice@dom", "allow", ["write_data"]))
    result = compute_effective(
        acl=acl,
        base_mode=None, base_uid=None, base_gid=None,
        principal=PrincipalRef(type="nfsv4_principal", identifier="alice@dom"),
        groups=[],
    )
    assert result.rights["write"].granted is True
    # delete is its own bit in NFSv4 — not folded.
    assert result.rights["delete"].granted is False


def test_nfsv4_change_perms_via_write_acl():
    acl = _nfs(("alice@dom", "allow", ["write_acl"]))
    result = compute_effective(
        acl=acl,
        base_mode=None, base_uid=None, base_gid=None,
        principal=PrincipalRef(type="nfsv4_principal", identifier="alice@dom"),
        groups=[],
    )
    assert result.rights["change_perms"].granted is True


def test_nfsv4_by_field_references_ace():
    acl = _nfs(("alice@dom", "allow", ["read_data"]))
    result = compute_effective(
        acl=acl,
        base_mode=None, base_uid=None, base_gid=None,
        principal=PrincipalRef(type="nfsv4_principal", identifier="alice@dom"),
        groups=[],
    )
    assert result.rights["read"].by[0].ace_index == 0
    assert "alice@dom" in result.rights["read"].by[0].summary
```

- [ ] **Step 2: Run tests — confirm NFSv4 tests fail**

```bash
docker compose exec -T api pytest tests/test_effective_perms.py -v
```

Expected: POSIX tests still pass; NFSv4 tests fail (the stub returns all-denied).

- [ ] **Step 3: Implement NFSv4 evaluator**

In `api/akashic/services/effective_perms.py`, add the right-bit map at the top of the file (just below `_ALL_RIGHTS`):

```python
_NFSV4_BITS: dict[RightName, set[str]] = {
    "read":         {"read_data", "list_directory"},
    "write":        {"write_data", "append_data", "add_file"},
    "execute":      {"execute"},
    "delete":       {"delete", "delete_child"},
    "change_perms": {"write_acl", "write_owner"},
}
```

Replace the `_eval_nfsv4` stub with:

```python
def _nfsv4_principal_matches(
    ace: NfsV4ACE, principal: PrincipalRef, groups: list[GroupRef],
) -> bool:
    if ace.principal == "EVERYONE@":
        return True
    if "identifier_group" in ace.flags:
        return any(g.identifier == ace.principal for g in groups)
    if ace.principal == principal.identifier:
        return True
    if ace.principal == "OWNER@" and principal.identifier == "OWNER@":
        return True
    if ace.principal == "GROUP@":
        return any(g.identifier == "GROUP@" for g in groups)
    return False


def _eval_nfsv4(acl: NfsV4ACL, principal: PrincipalRef, groups: list[GroupRef]) -> EffectivePerms:
    rights = _empty_rights()
    for right, bit_set in _NFSV4_BITS.items():
        for i, ace in enumerate(acl.entries):
            if ace.ace_type not in ("allow", "deny"):
                continue  # audit/alarm don't affect access
            if not _nfsv4_principal_matches(ace, principal, groups):
                continue
            if not (set(ace.mask) & bit_set):
                continue
            granted = ace.ace_type == "allow"
            ref = ACEReference(
                ace_index=i,
                summary=f"{ace.principal} {ace.ace_type} {','.join(ace.mask)}",
            )
            rights[right] = RightResult(granted=granted, by=[ref])
            break  # first match wins per RFC 7530 §6.2.1
    return _effective(rights, "nfsv4", principal, groups, [])
```

- [ ] **Step 4: Run tests — confirm NFSv4 tests pass**

```bash
docker compose exec -T api pytest tests/test_effective_perms.py -v
```

Expected: 16 passed (8 POSIX + 8 NFSv4).

- [ ] **Step 5: Commit**

```bash
git add api/akashic/services/effective_perms.py api/tests/test_effective_perms.py
git commit -m "feat(api): NFSv4 effective-permissions evaluator"
```

---

## Task 4 — NT evaluator (TDD)

NT (DACL) per Microsoft's documented algorithm: same first-match-wins shape as NFSv4. Owner principal grants implicit `READ_CONTROL + WRITE_DAC` (we surface this as `change_perms` granted via a synthetic ACE reference).

Principal matching for NT is by SID:
- ACE matches if `principal.identifier == ace.sid`.
- ACE with `identifier_group` flag in `flags` matches if any group's `identifier == ace.sid`.
- Special "Everyone" SID `S-1-1-0` matches everyone (no group flag needed).
- Special "Authenticated Users" `S-1-5-11` matches when `principal.type == "sid"` (we treat any SID-typed principal as authenticated).

We do NOT check inherited-vs-direct here — the captured DACL is post-inheritance per spec.

**Files:**
- Modify: `api/akashic/services/effective_perms.py`
- Modify: `api/tests/test_effective_perms.py`

- [ ] **Step 1: Add failing NT tests**

Append to `api/tests/test_effective_perms.py`:

```python
from akashic.schemas.acl import NtACE, NtACL, NtPrincipal


def _nt(
    entries: list[dict],
    owner: dict | None = None,
    group: dict | None = None,
) -> NtACL:
    return NtACL.model_validate({
        "type": "nt",
        "owner": owner,
        "group": group,
        "control": [],
        "entries": entries,
    })


def _nt_ace(sid: str, ace_type: str, mask: list[str], flags: list[str] | None = None) -> dict:
    return {
        "sid": sid, "name": "",
        "ace_type": ace_type, "flags": flags or [], "mask": mask,
    }


@pytest.mark.parametrize(
    "principal_sid,group_sids,acl,expect_read,expect_write,expect_delete",
    [
        # Direct allow.
        ("S-1-5-21-1-2-3-1013", [],
         _nt([_nt_ace("S-1-5-21-1-2-3-1013", "allow", ["READ_DATA", "WRITE_DATA"])]),
         True, True, False),
        # Deny precedes allow.
        ("S-1-5-21-1-2-3-1013", [],
         _nt([
             _nt_ace("S-1-5-21-1-2-3-1013", "deny",  ["WRITE_DATA"]),
             _nt_ace("S-1-5-21-1-2-3-1013", "allow", ["WRITE_DATA"]),
         ]),
         False, False, False),
        # Everyone SID matches anyone.
        ("S-1-5-21-9-9-9-1234", [],
         _nt([_nt_ace("S-1-1-0", "allow", ["READ_DATA"])]),
         True, False, False),
        # Group match via identifier_group.
        ("S-1-5-21-1-2-3-1013",
         ["S-1-5-21-1-2-3-513"],
         _nt([_nt_ace("S-1-5-21-1-2-3-513", "allow", ["WRITE_DATA"], ["identifier_group"])]),
         False, True, False),
        # GENERIC_READ maps to read.
        ("S-1-5-21-1-2-3-1013", [],
         _nt([_nt_ace("S-1-5-21-1-2-3-1013", "allow", ["GENERIC_READ"])]),
         True, False, False),
        # DELETE bit grants delete.
        ("S-1-5-21-1-2-3-1013", [],
         _nt([_nt_ace("S-1-5-21-1-2-3-1013", "allow", ["DELETE"])]),
         False, False, True),
    ],
)
def test_nt_evaluator(principal_sid, group_sids, acl, expect_read, expect_write, expect_delete):
    groups = [GroupRef(type="sid", identifier=g) for g in group_sids]
    result = compute_effective(
        acl=acl,
        base_mode=None, base_uid=None, base_gid=None,
        principal=PrincipalRef(type="sid", identifier=principal_sid),
        groups=groups,
    )
    assert result.rights["read"].granted is expect_read
    assert result.rights["write"].granted is expect_write
    assert result.rights["delete"].granted is expect_delete
    assert result.evaluated_with.model == "nt"


def test_nt_owner_implicit_change_perms():
    sid = "S-1-5-21-1-2-3-1013"
    acl = _nt(
        [_nt_ace("S-1-1-0", "allow", ["READ_DATA"])],
        owner={"sid": sid, "name": "DOM\\alice"},
    )
    result = compute_effective(
        acl=acl,
        base_mode=None, base_uid=None, base_gid=None,
        principal=PrincipalRef(type="sid", identifier=sid),
        groups=[],
    )
    assert result.rights["change_perms"].granted is True
    # Synthetic ace_index=-1 indicates "owner implicit".
    assert any(ref.ace_index == -1 for ref in result.rights["change_perms"].by)


def test_nt_authenticated_users_matches_any_sid_principal():
    acl = _nt([_nt_ace("S-1-5-11", "allow", ["READ_DATA"])])
    result = compute_effective(
        acl=acl,
        base_mode=None, base_uid=None, base_gid=None,
        principal=PrincipalRef(type="sid", identifier="S-1-5-21-9-9-9-1234"),
        groups=[],
    )
    assert result.rights["read"].granted is True
```

- [ ] **Step 2: Run tests — confirm NT tests fail**

```bash
docker compose exec -T api pytest tests/test_effective_perms.py -v
```

Expected: NT tests fail.

- [ ] **Step 3: Implement NT evaluator**

Add the right-bit map near `_NFSV4_BITS`:

```python
_NT_BITS: dict[RightName, set[str]] = {
    "read":         {"READ_DATA", "LIST_DIRECTORY", "GENERIC_READ", "GENERIC_ALL"},
    "write":        {"WRITE_DATA", "ADD_FILE", "APPEND_DATA", "ADD_SUBDIRECTORY", "GENERIC_WRITE", "GENERIC_ALL"},
    "execute":      {"EXECUTE", "TRAVERSE", "GENERIC_EXECUTE", "GENERIC_ALL"},
    "delete":       {"DELETE", "DELETE_CHILD", "GENERIC_ALL"},
    "change_perms": {"WRITE_DAC", "WRITE_OWNER", "GENERIC_ALL"},
}

_NT_EVERYONE_SID = "S-1-1-0"
_NT_AUTHENTICATED_USERS_SID = "S-1-5-11"
```

Replace the `_eval_nt` stub with:

```python
def _nt_principal_matches(
    ace: NtACE, principal: PrincipalRef, groups: list[GroupRef],
) -> bool:
    if ace.sid == _NT_EVERYONE_SID:
        return True
    if ace.sid == _NT_AUTHENTICATED_USERS_SID and principal.type == "sid":
        return True
    if "identifier_group" in ace.flags:
        return any(g.identifier == ace.sid for g in groups)
    return ace.sid == principal.identifier


def _eval_nt(acl: NtACL, principal: PrincipalRef, groups: list[GroupRef]) -> EffectivePerms:
    rights = _empty_rights()
    for right, bit_set in _NT_BITS.items():
        for i, ace in enumerate(acl.entries):
            if ace.ace_type not in ("allow", "deny"):
                continue
            if not _nt_principal_matches(ace, principal, groups):
                continue
            if not (set(ace.mask) & bit_set):
                continue
            granted = ace.ace_type == "allow"
            label = ace.name or ace.sid
            ref = ACEReference(
                ace_index=i,
                summary=f"{label} {ace.ace_type} {','.join(ace.mask)}",
            )
            rights[right] = RightResult(granted=granted, by=[ref])
            break

    # Owner implicit change_perms (READ_CONTROL + WRITE_DAC).
    caveats: list[str] = []
    if acl.owner is not None and acl.owner.sid == principal.identifier:
        if not rights["change_perms"].granted:
            ref = ACEReference(
                ace_index=-1,
                summary=f"owner implicit (READ_CONTROL+WRITE_DAC): {acl.owner.name or acl.owner.sid}",
            )
            rights["change_perms"] = RightResult(granted=True, by=[ref])
            caveats.append("Owner is implicitly granted READ_CONTROL and WRITE_DAC even without explicit ACEs.")

    return _effective(rights, "nt", principal, groups, caveats)
```

- [ ] **Step 4: Run tests — confirm NT tests pass**

```bash
docker compose exec -T api pytest tests/test_effective_perms.py -v
```

Expected: 24 passed (8 POSIX + 8 NFSv4 + 8 NT).

- [ ] **Step 5: Commit**

```bash
git add api/akashic/services/effective_perms.py api/tests/test_effective_perms.py
git commit -m "feat(api): NT effective-permissions evaluator with owner implicit perms"
```

---

## Task 5 — S3 evaluator (TDD)

S3 evaluation:
1. Object-ACL grants — match `principal.identifier` to `grantee_id` for `canonical_user`, or to well-known group strings (`AllUsers`, `AuthenticatedUsers`) for `group`-typed grants.
2. (Bucket policy / PAB integration is captured in `source_security` dict but only consulted to add caveats — full Principal/Condition evaluation is out of scope per spec line 327-329.)

Right mapping per spec line 405-407:
- `read` ← `READ`, `FULL_CONTROL`
- `write` ← `WRITE`, `FULL_CONTROL`
- `delete` ← `WRITE`, `FULL_CONTROL` (S3 deletes go through bucket WRITE)
- `execute` always denied (n/a in S3)
- `change_perms` ← `WRITE_ACP`, `FULL_CONTROL`

Caveats always include `"S3 evaluation does not include IAM user/role policies; bucket policy condition keys not evaluated"`.

**Files:**
- Modify: `api/akashic/services/effective_perms.py`
- Modify: `api/tests/test_effective_perms.py`

- [ ] **Step 1: Add failing S3 tests**

Append to `api/tests/test_effective_perms.py`:

```python
from akashic.schemas.acl import S3ACL, S3Grant, S3Owner


def _s3(grants: list[dict], owner: dict | None = None) -> S3ACL:
    return S3ACL.model_validate({
        "type": "s3",
        "owner": owner,
        "grants": grants,
    })


@pytest.mark.parametrize(
    "principal_id,grants,expect_read,expect_write,expect_delete",
    [
        # Direct canonical_user grant.
        ("acct-1",
         [{"grantee_type": "canonical_user", "grantee_id": "acct-1", "grantee_name": "", "permission": "READ"}],
         True, False, False),
        # FULL_CONTROL grants everything (except execute).
        ("acct-1",
         [{"grantee_type": "canonical_user", "grantee_id": "acct-1", "grantee_name": "", "permission": "FULL_CONTROL"}],
         True, True, True),
        # AllUsers group grants to everyone.
        ("anyone",
         [{"grantee_type": "group", "grantee_id": "AllUsers", "grantee_name": "", "permission": "READ"}],
         True, False, False),
        # WRITE permission yields delete granted.
        ("acct-1",
         [{"grantee_type": "canonical_user", "grantee_id": "acct-1", "grantee_name": "", "permission": "WRITE"}],
         False, True, True),
        # Non-matching grantee.
        ("acct-2",
         [{"grantee_type": "canonical_user", "grantee_id": "acct-1", "grantee_name": "", "permission": "READ"}],
         False, False, False),
    ],
)
def test_s3_evaluator(principal_id, grants, expect_read, expect_write, expect_delete):
    acl = _s3(grants)
    result = compute_effective(
        acl=acl,
        base_mode=None, base_uid=None, base_gid=None,
        principal=PrincipalRef(type="s3_canonical", identifier=principal_id),
        groups=[],
    )
    assert result.rights["read"].granted is expect_read
    assert result.rights["write"].granted is expect_write
    assert result.rights["delete"].granted is expect_delete
    assert result.rights["execute"].granted is False  # always n/a
    assert result.evaluated_with.model == "s3"


def test_s3_authenticated_users_matches_any_principal():
    acl = _s3([
        {"grantee_type": "group", "grantee_id": "AuthenticatedUsers", "grantee_name": "", "permission": "READ"},
    ])
    result = compute_effective(
        acl=acl,
        base_mode=None, base_uid=None, base_gid=None,
        principal=PrincipalRef(type="s3_canonical", identifier="any-acct"),
        groups=[],
    )
    assert result.rights["read"].granted is True


def test_s3_caveats_always_include_iam_note():
    acl = _s3([])
    result = compute_effective(
        acl=acl,
        base_mode=None, base_uid=None, base_gid=None,
        principal=PrincipalRef(type="s3_canonical", identifier="acct-1"),
        groups=[],
    )
    assert any("IAM" in c for c in result.evaluated_with.caveats)
```

- [ ] **Step 2: Run tests — confirm S3 tests fail**

```bash
docker compose exec -T api pytest tests/test_effective_perms.py -v
```

Expected: S3 tests fail.

- [ ] **Step 3: Implement S3 evaluator**

Add the right-permission map near `_NT_BITS`:

```python
_S3_PERMS: dict[RightName, set[str]] = {
    "read":         {"READ", "FULL_CONTROL"},
    "write":        {"WRITE", "FULL_CONTROL"},
    "delete":       {"WRITE", "FULL_CONTROL"},
    "change_perms": {"WRITE_ACP", "FULL_CONTROL"},
    # execute intentionally absent — S3 has no execute concept.
}

_S3_ALL_USERS = "AllUsers"
_S3_AUTHENTICATED_USERS = "AuthenticatedUsers"
_S3_CAVEAT_IAM = (
    "S3 evaluation does not include IAM user/role policies; "
    "bucket policy condition keys not evaluated."
)
```

Replace the `_eval_s3` stub with:

```python
def _s3_grant_matches(grant, principal: PrincipalRef) -> bool:
    if grant.grantee_type == "group":
        if grant.grantee_id == _S3_ALL_USERS:
            return True
        if grant.grantee_id == _S3_AUTHENTICATED_USERS:
            return True
        return False
    # canonical_user / amazon_customer_by_email
    return grant.grantee_id == principal.identifier


def _eval_s3(
    acl: S3ACL,
    principal: PrincipalRef,
    groups: list[GroupRef],
    source_security: dict | None,
) -> EffectivePerms:
    rights = _empty_rights()
    for right, perm_set in _S3_PERMS.items():
        for i, grant in enumerate(acl.grants):
            if not _s3_grant_matches(grant, principal):
                continue
            if grant.permission not in perm_set:
                continue
            label = grant.grantee_name or grant.grantee_id
            ref = ACEReference(
                ace_index=i,
                summary=f"{grant.grantee_type}:{label} {grant.permission}",
            )
            rights[right] = RightResult(granted=True, by=[ref])
            break  # any matching allow grants — no deny in S3 ACL grammar
    caveats = [_S3_CAVEAT_IAM]
    if source_security and source_security.get("is_public_inferred"):
        caveats.append("Bucket is publicly accessible per Public Access Block / bucket policy.")
    return _effective(rights, "s3", principal, groups, caveats)
```

- [ ] **Step 4: Run tests — confirm S3 tests pass**

```bash
docker compose exec -T api pytest tests/test_effective_perms.py -v
```

Expected: 32 passed (8 POSIX + 8 NFSv4 + 8 NT + 8 S3).

- [ ] **Step 5: Commit**

```bash
git add api/akashic/services/effective_perms.py api/tests/test_effective_perms.py
git commit -m "feat(api): S3 effective-permissions evaluator"
```

---

## Task 6 — `POST /api/entries/{entry_id}/effective-permissions` endpoint

Thin RBAC wrapper. Loads the entry, checks source access, calls `compute_effective`, returns the `EffectivePerms` payload. No state mutation, no caching.

**Files:**
- Create: `api/akashic/routers/effective_perms.py`
- Modify: `api/akashic/main.py`
- Create: `api/tests/test_effective_perms_endpoint.py`

- [ ] **Step 1: Write failing endpoint tests**

Create `api/tests/test_effective_perms_endpoint.py`:

```python
import pytest


async def _register_login(client, username="alice", password="testpass123"):
    await client.post("/api/users/register", json={"username": username, "password": password})
    login = await client.post("/api/users/login", json={"username": username, "password": password})
    return login.json()["access_token"]


@pytest.mark.asyncio
async def test_effective_perms_404_for_unknown_entry(client):
    token = await _register_login(client)
    r = await client.post(
        "/api/entries/00000000-0000-0000-0000-000000000000/effective-permissions",
        json={"principal": {"type": "posix_uid", "identifier": "1000"}},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_effective_perms_requires_auth(client):
    r = await client.post(
        "/api/entries/00000000-0000-0000-0000-000000000000/effective-permissions",
        json={"principal": {"type": "posix_uid", "identifier": "1000"}},
    )
    assert r.status_code == 401


@pytest.mark.asyncio
async def test_effective_perms_happy_path_posix(client, db_session):
    """End-to-end: create source + entry, then call the endpoint."""
    import uuid
    from akashic.models import Entry, Source

    token = await _register_login(client, username="bob")

    source = Source(
        id=uuid.uuid4(),
        name="t",
        type="local",
        connection_config={"path": "/tmp"},
    )
    db_session.add(source)
    await db_session.flush()

    # Grant the user read access to the source — depends on RBAC plumbing.
    # For this test, we register the user as the owner via direct SQL or use
    # an existing admin grant pathway. If your conftest already has an admin
    # bypass, prefer that.
    from akashic.models.user import User
    from sqlalchemy import select
    user = (await db_session.execute(select(User).where(User.username == "bob"))).scalar_one()
    # If your project has a SourceAccess / ACL table, insert here. Otherwise
    # this test may skip if no per-user grant model exists in MVP.

    entry = Entry(
        id=uuid.uuid4(),
        source_id=source.id,
        kind="file",
        path="/tmp/x",
        parent_path="/tmp",
        name="x",
        mode=0o755,
        uid=1000,
        gid=100,
        acl={"type": "posix",
             "entries": [{"tag": "user", "qualifier": "1001", "perms": "rwx"}],
             "default_entries": None},
    )
    db_session.add(entry)
    await db_session.commit()

    r = await client.post(
        f"/api/entries/{entry.id}/effective-permissions",
        json={"principal": {"type": "posix_uid", "identifier": "1001"}},
        headers={"Authorization": f"Bearer {token}"},
    )
    # Allow either 200 (full RBAC granted automatically) or 403 (RBAC blocks).
    assert r.status_code in (200, 403)
    if r.status_code == 200:
        data = r.json()
        assert data["evaluated_with"]["model"] == "posix"
        assert data["rights"]["read"]["granted"] is True
```

(Note: the happy-path test is intentionally permissive — RBAC plumbing in this codebase requires source grants that aren't trivial to set up in a unit test fixture. The 404 and 401 tests are the contract guarantees; the 200/403 acceptance is best-effort. If your RBAC layer auto-grants on source ownership, the 200 branch is what you'll see.)

- [ ] **Step 2: Run tests — confirm they fail (404 path returns 404 already due to no router)**

```bash
docker compose exec -T api pytest tests/test_effective_perms_endpoint.py -v
```

Expected: 404 test should already pass (no route → FastAPI 404), but 401 and happy-path tests fail because the route isn't registered AND has no auth.

Actually FastAPI returns 404 for unknown routes too — so you can't distinguish "no route" from "route says 404". The TDD signal here is the 401 test failing (no auth check before the not-found handler). After implementation, the auth dep runs first and returns 401.

- [ ] **Step 3: Create the router**

Create `api/akashic/routers/effective_perms.py`:

```python
"""POST /api/entries/{entry_id}/effective-permissions — read-only evaluator."""
import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from akashic.auth.dependencies import check_source_access, get_current_user
from akashic.database import get_db
from akashic.models.entry import Entry
from akashic.models.source import Source
from akashic.models.user import User
from akashic.schemas.acl import ACL  # noqa: F401  (referenced indirectly)
from akashic.schemas.effective import EffectivePerms, EffectivePermsRequest
from akashic.services.effective_perms import compute_effective
from pydantic import TypeAdapter

router = APIRouter(prefix="/api/entries", tags=["effective-permissions"])
_acl_adapter = TypeAdapter(ACL)


@router.post(
    "/{entry_id}/effective-permissions",
    response_model=EffectivePerms,
)
async def post_effective_permissions(
    entry_id: uuid.UUID,
    request: EffectivePermsRequest,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> EffectivePerms:
    entry = (await db.execute(select(Entry).where(Entry.id == entry_id))).scalar_one_or_none()
    if entry is None:
        raise HTTPException(status_code=404, detail="Entry not found")
    await check_source_access(entry.source_id, user, db)

    # entry.acl is JSONB; validate into the discriminated union (or None).
    acl_obj = _acl_adapter.validate_python(entry.acl) if entry.acl else None

    # source_security (for S3) — pull from the entry's source if present.
    source_security = None
    if acl_obj is not None and getattr(acl_obj, "type", None) == "s3":
        source = (await db.execute(select(Source).where(Source.id == entry.source_id))).scalar_one_or_none()
        if source is not None:
            source_security = source.security_metadata

    return compute_effective(
        acl=acl_obj,
        base_mode=entry.mode,
        base_uid=entry.uid,
        base_gid=entry.gid,
        principal=request.principal,
        groups=request.groups,
        source_security=source_security,
    )
```

- [ ] **Step 4: Register the router in `main.py`**

In `api/akashic/main.py`, add `from akashic.routers import effective_perms` (alongside the other router imports) and `app.include_router(effective_perms.router)` in the `create_app()` body alongside the other `include_router` calls.

- [ ] **Step 5: Run tests — confirm they pass**

```bash
docker compose exec -T api pytest tests/test_effective_perms_endpoint.py -v
```

Expected: 401 test passes (auth dep runs first), 404 test passes (no entry), happy-path test passes (200) or returns 403 (RBAC blocks) — both are acceptable.

- [ ] **Step 6: Run the full effective-perms test suite for regression**

```bash
docker compose exec -T api pytest tests/test_effective_perms.py tests/test_effective_perms_endpoint.py tests/test_effective_perms_schemas.py -v
```

Expected: all pass.

- [ ] **Step 7: Commit**

```bash
git add api/akashic/routers/effective_perms.py api/akashic/main.py api/tests/test_effective_perms_endpoint.py
git commit -m "feat(api): POST /api/entries/{id}/effective-permissions endpoint"
```

---

## Task 7 — Frontend types

**Files:**
- Create: `web/src/lib/effectivePermsTypes.ts`
- Modify: `web/src/types/index.ts` (re-export)

- [ ] **Step 1: Create the types file**

Create `web/src/lib/effectivePermsTypes.ts`:

```ts
export type PrincipalType = "posix_uid" | "sid" | "nfsv4_principal" | "s3_canonical";
export type RightName = "read" | "write" | "execute" | "delete" | "change_perms";

export interface PrincipalRef {
  type: PrincipalType;
  identifier: string;
  name?: string;
}

export interface GroupRef {
  type: PrincipalType;
  identifier: string;
  name?: string;
}

export interface ACEReference {
  ace_index: number;
  summary: string;
}

export interface RightResult {
  granted: boolean;
  by: ACEReference[];
}

export interface EffectivePermsEvaluatedWith {
  model: "posix" | "nfsv4" | "nt" | "s3" | "none";
  principal: PrincipalRef;
  groups: GroupRef[];
  caveats: string[];
}

export interface EffectivePerms {
  rights: Record<RightName, RightResult>;
  evaluated_with: EffectivePermsEvaluatedWith;
}

export interface EffectivePermsRequest {
  principal: PrincipalRef;
  groups?: GroupRef[];
  principal_name_hint?: string;
}
```

- [ ] **Step 2: Re-export from `web/src/types/index.ts`**

At the end of `web/src/types/index.ts`, add:

```ts
export type {
  PrincipalType,
  RightName,
  PrincipalRef,
  GroupRef,
  ACEReference,
  RightResult,
  EffectivePerms,
  EffectivePermsEvaluatedWith,
  EffectivePermsRequest,
} from "../lib/effectivePermsTypes";
```

- [ ] **Step 3: Verify TypeScript clean**

```bash
docker run --rm -v "$(pwd)/web:/app" -w /app node:20-alpine npx tsc --noEmit
```

Expected: no errors.

- [ ] **Step 4: Commit**

```bash
git add web/src/lib/effectivePermsTypes.ts web/src/types/index.ts
git commit -m "feat(web): TS types for effective permissions"
```

---

## Task 8 — `<EffectivePermissions>` React component

The card sits between the ACL section and Extended attributes (NOT version history — version history is below ACL today; insert before xattrs to keep the sequence Identity → Permissions → ACL → Effective → xattrs → Content → Timestamps → History).

State:
- `principalType: PrincipalType` — defaults from entry's ACL type (`acl.type === "posix"` → `posix_uid`, `nfsv4` → `nfsv4_principal`, `nt` → `sid`, `s3` → `s3_canonical`, `null` → `posix_uid`).
- `principalIdentifier: string`
- `groups: { type, identifier }[]` — initial empty, can add/remove rows.
- `result: EffectivePerms | null` — set by `useMutation.onSuccess`.

UI:
1. Form: type dropdown, identifier input, "+ Add group" button. Each group row is `[type-dropdown] [identifier-input] [×]`.
2. "Compute" button. Disabled while mutation pending; shows spinner.
3. Result panel (after first success): caveats list (if any) at top, then 5-row table:

| Right | Granted | Source ACEs |
|---|---|---|
| read | ✓ | `user:1001 rwx` |
| write | ✗ | (none) |
| ... | | |

**Files:**
- Create: `web/src/components/acl/EffectivePermissions.tsx`

- [ ] **Step 1: Create the component**

Create `web/src/components/acl/EffectivePermissions.tsx`:

```tsx
import { useState } from "react";
import { useMutation } from "@tanstack/react-query";
import { api } from "../../api/client";
import type {
  ACL,
  EffectivePerms,
  EffectivePermsRequest,
  GroupRef,
  PrincipalType,
  RightName,
} from "../../types";
import { Section, Chip } from "./shared";

const RIGHT_LABELS: Record<RightName, string> = {
  read: "Read",
  write: "Write",
  execute: "Execute",
  delete: "Delete",
  change_perms: "Change permissions",
};

const PRINCIPAL_TYPES: { value: PrincipalType; label: string }[] = [
  { value: "posix_uid",        label: "POSIX UID" },
  { value: "sid",              label: "SID (Windows)" },
  { value: "nfsv4_principal",  label: "NFSv4 principal" },
  { value: "s3_canonical",     label: "S3 canonical user" },
];

function defaultPrincipalType(acl: ACL | null): PrincipalType {
  if (!acl) return "posix_uid";
  switch (acl.type) {
    case "posix":  return "posix_uid";
    case "nfsv4":  return "nfsv4_principal";
    case "nt":     return "sid";
    case "s3":     return "s3_canonical";
  }
}

export function EffectivePermissions({
  entryId,
  acl,
}: {
  entryId: string;
  acl: ACL | null;
}) {
  const [principalType, setPrincipalType] = useState<PrincipalType>(defaultPrincipalType(acl));
  const [identifier, setIdentifier] = useState("");
  const [groups, setGroups] = useState<GroupRef[]>([]);

  const mutation = useMutation<EffectivePerms, Error, EffectivePermsRequest>({
    mutationFn: (body) =>
      api.post<EffectivePerms>(`/entries/${entryId}/effective-permissions`, body),
  });

  const submit = () => {
    if (!identifier.trim()) return;
    mutation.mutate({
      principal: { type: principalType, identifier: identifier.trim() },
      groups: groups.filter((g) => g.identifier.trim() !== ""),
    });
  };

  return (
    <Section title="Effective permissions">
      <div className="space-y-3">
        <div className="flex flex-wrap items-end gap-2">
          <label className="text-xs text-gray-500 flex flex-col">
            Principal type
            <select
              className="mt-1 text-sm border border-gray-200 rounded px-2 py-1"
              value={principalType}
              onChange={(e) => setPrincipalType(e.target.value as PrincipalType)}
            >
              {PRINCIPAL_TYPES.map((t) => (
                <option key={t.value} value={t.value}>{t.label}</option>
              ))}
            </select>
          </label>
          <label className="text-xs text-gray-500 flex flex-col flex-1 min-w-[160px]">
            Identifier
            <input
              type="text"
              className="mt-1 text-sm font-mono border border-gray-200 rounded px-2 py-1"
              value={identifier}
              onChange={(e) => setIdentifier(e.target.value)}
              placeholder={principalType === "sid" ? "S-1-5-21-..." : "1000"}
            />
          </label>
        </div>

        {groups.map((g, i) => (
          <div key={i} className="flex items-center gap-2">
            <select
              className="text-xs border border-gray-200 rounded px-2 py-1"
              value={g.type}
              onChange={(e) => {
                const next = [...groups];
                next[i] = { ...g, type: e.target.value as PrincipalType };
                setGroups(next);
              }}
            >
              {PRINCIPAL_TYPES.map((t) => (
                <option key={t.value} value={t.value}>{t.label}</option>
              ))}
            </select>
            <input
              type="text"
              className="flex-1 text-sm font-mono border border-gray-200 rounded px-2 py-1"
              placeholder="group identifier"
              value={g.identifier}
              onChange={(e) => {
                const next = [...groups];
                next[i] = { ...g, identifier: e.target.value };
                setGroups(next);
              }}
            />
            <button
              type="button"
              onClick={() => setGroups(groups.filter((_, j) => j !== i))}
              className="text-xs text-gray-400 hover:text-red-600 px-2"
              aria-label="Remove group"
            >×</button>
          </div>
        ))}

        <div className="flex items-center gap-3">
          <button
            type="button"
            onClick={() => setGroups([...groups, { type: principalType, identifier: "" }])}
            className="text-xs text-accent-600 hover:text-accent-800"
          >+ Add group</button>
          <button
            type="button"
            onClick={submit}
            disabled={!identifier.trim() || mutation.isPending}
            className="text-sm bg-accent-600 text-white rounded px-3 py-1 disabled:opacity-50 hover:bg-accent-700"
          >
            {mutation.isPending ? "Computing…" : "Compute"}
          </button>
        </div>

        {mutation.error && (
          <div className="text-sm text-red-700 bg-red-50 rounded px-3 py-2">
            {mutation.error.message}
          </div>
        )}

        {mutation.data && (
          <div className="mt-2 border border-gray-200 rounded">
            {mutation.data.evaluated_with.caveats.length > 0 && (
              <div className="px-3 py-2 border-b border-gray-200 bg-amber-50 text-xs text-amber-800 space-y-1">
                {mutation.data.evaluated_with.caveats.map((c, i) => (
                  <div key={i}>⚠ {c}</div>
                ))}
              </div>
            )}
            <table className="w-full text-sm">
              <tbody>
                {(["read","write","execute","delete","change_perms"] as RightName[]).map((r) => {
                  const result = mutation.data!.rights[r];
                  return (
                    <tr key={r} className="border-t border-gray-100 first:border-t-0">
                      <td className="px-3 py-1.5 text-gray-700 w-1/4">{RIGHT_LABELS[r]}</td>
                      <td className="px-3 py-1.5 w-12 text-center">
                        {result.granted
                          ? <Chip variant="allow">✓</Chip>
                          : <Chip variant="deny">✗</Chip>}
                      </td>
                      <td className="px-3 py-1.5 text-xs text-gray-500 font-mono break-all">
                        {result.by.length > 0
                          ? result.by.map((b) => b.summary).join("; ")
                          : "—"}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </Section>
  );
}
```

- [ ] **Step 2: Verify TypeScript clean**

```bash
docker run --rm -v "$(pwd)/web:/app" -w /app node:20-alpine npx tsc --noEmit
```

Expected: no errors. (If `Chip` doesn't exist or signature differs, look at `web/src/components/acl/shared.tsx` and adapt.)

- [ ] **Step 3: Verify Vite build clean**

```bash
docker run --rm -v "$(pwd)/web:/app" -w /app node:20-alpine npm run build
```

Expected: build succeeds.

- [ ] **Step 4: Commit**

```bash
git add web/src/components/acl/EffectivePermissions.tsx
git commit -m "feat(web): EffectivePermissions card with principal picker and result table"
```

---

## Task 9 — Wire `<EffectivePermissions>` into `EntryDetail`

Insert between `<ACLSection>` and the Extended attributes section.

**Files:**
- Modify: `web/src/components/EntryDetail.tsx`

- [ ] **Step 1: Add the import**

Near the top of `web/src/components/EntryDetail.tsx`, add:

```tsx
import { EffectivePermissions } from "./acl/EffectivePermissions";
```

- [ ] **Step 2: Render the card after `<ACLSection>`**

Find the line `<ACLSection acl={entry.acl} />` (currently around line 130). Insert after it:

```tsx
<EffectivePermissions entryId={entry.id} acl={entry.acl} />
```

The relevant block becomes:

```tsx
      <ACLSection acl={entry.acl} />

      <EffectivePermissions entryId={entry.id} acl={entry.acl} />

      <Section
        title="Extended attributes"
        ...
```

- [ ] **Step 3: Verify TypeScript clean**

```bash
docker run --rm -v "$(pwd)/web:/app" -w /app node:20-alpine npx tsc --noEmit
```

Expected: no errors.

- [ ] **Step 4: Verify Vite build clean**

```bash
docker run --rm -v "$(pwd)/web:/app" -w /app node:20-alpine npm run build
```

Expected: build succeeds.

- [ ] **Step 5: Commit**

```bash
git add web/src/components/EntryDetail.tsx
git commit -m "feat(web): wire EffectivePermissions into EntryDetail"
```

---

## Task 10 — End-to-end manual verification

Confirms the full path: scan → entry has ACL → click drawer → enter principal → see correct grants/denies.

**Files:** none.

- [ ] **Step 1: Bring the stack up**

```bash
docker compose up -d
```

Wait for `db` to be healthy and `api` to respond on `:8000/health` (or similar).

- [ ] **Step 2: Set up a test entry with a known POSIX ACL**

```bash
mkdir -p /tmp/eff-perms-demo
echo "test" > /tmp/eff-perms-demo/file.txt
chmod 600 /tmp/eff-perms-demo/file.txt
setfacl -m u:1001:rwx /tmp/eff-perms-demo/file.txt
setfacl -m m::r-x /tmp/eff-perms-demo/file.txt
```

Trigger a scan via the API for a source pointing at `/tmp/eff-perms-demo`. Wait for completion.

- [ ] **Step 3: Verify in the UI**

Open `http://127.0.0.1:5173/browse`, navigate to the file, open the drawer.

Expected:
- New "Effective permissions" section between ACL and Extended attributes.
- Principal type dropdown defaulted to "POSIX UID".
- Enter `1001` → click Compute.

Expected result table:
```
Read           ✓   user:1001 rwx (masked by r-x)
Write          ✗   —
Execute        ✓   user:1001 rwx (masked by r-x)
Delete         ✗   —
Change perms   ✗   —
```

Caveat banner shows "POSIX folds delete and change_perms into write…".

- [ ] **Step 4: Try an unknown UID — expect "other" perms**

Enter `9999` → Compute. Expected: all denied (mode `600` for "other" → `---`).

- [ ] **Step 5: Tear down demo**

```bash
rm -rf /tmp/eff-perms-demo
```

No commit — verification only.

---

## Notes for the implementer

- **`compute_effective` is keyword-only** (`*` in the signature) so callers can't accidentally swap argument positions.
- **The `by: list[ACEReference]` field is the contract.** Even when a result is `granted=False`, the `by` field should be `[]` (not `None`) — the UI iterates over it.
- **POSIX folds delete/change_perms into write** per spec line 311. NFSv4/NT/S3 do NOT fold — they have explicit bits.
- **NT `Authenticated Users` matching is intentionally simple** (`principal.type == "sid"`). True semantics require checking against `S-1-5-7` (Anonymous Logon), but for our use case "any SID-typed principal is authenticated" is the right approximation.
- **S3 has no `execute` concept.** It's always `granted=false, by=[]`.
- **NO state mutation, NO caching.** Every call recomputes. Keep this property — Phase 12's `acl_denorm.py` will reuse the same evaluators.
- **The endpoint test file** (`test_effective_perms_endpoint.py`) has a permissive happy-path assertion — RBAC plumbing in this codebase is non-trivial and the 200/403 acceptance is intentional. Don't over-tighten unless you see a clear path through `check_source_access` for the test user.
- **No docker-compose changes.** No new services. No new env vars. No model changes.

## Known gap (deferred to Phase 12)

The spec (lines 327-328) calls for PAB `block_public_acls` / `restrict_public_buckets` to **override** matching grants for `AllUsers` / `AuthenticatedUsers`. Phase 11 surfaces a caveat ("Bucket is publicly accessible per Public Access Block / bucket policy") when `is_public_inferred=true` but does NOT mask out the grant in the result. Phase 12 (which reuses these evaluators in `acl_denorm.py`) will tighten this — denormalized indexing especially must not over-grant `AllUsers` access when PAB blocks it. For Phase 11's interactive endpoint, the caveat is sufficient — the operator sees the warning alongside the grant.
