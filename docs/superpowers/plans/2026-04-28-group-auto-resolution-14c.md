# Phase 14c — Group-Membership Auto-Resolution (NT SMB / SAMR) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add NT SMB group resolution to the existing `services/group_resolver.py` dispatcher. After Phase 14b, `(source.type='smb', identity_type='sid')` raises `UnsupportedResolution`. After this phase it issues SAMR calls over a DCE/RPC binding on the `\PIPE\samr` named pipe (via the existing SMB IPC$ mount path Phase 9 set up for LSARPC) to resolve the user's group memberships, returning group names.

**Out of scope:** UI changes (nothing changes for the user — the existing "Resolve groups" button works for SMB sources once this lands), schema changes, support for non-domain-joined NT machines (those would need MS-SAMR alternative flows we don't need yet).

---

## Architecture

The work splits into three chunks:

1. **Go SAMR package** — new `scanner/internal/samr/` with DCE/RPC bind, NDR encoding/decoding for SAMR-specific types, and per-opcode request builders + response parsers. Mirrors the structure of `scanner/internal/lsarpc/`.
2. **Scanner CLI subcommand** — new `akashic-scanner resolve-groups --type=smb --host=… --user=… --password=… --sid=<S-1-5-21-…>` that mounts SMB IPC$, opens `\PIPE\samr`, runs the SAMR call sequence, and prints `{"groups":["users","domain-admins"],"source":"samr"}` to stdout.
3. **API integration** — extend `services/group_resolver.py`'s dispatcher to spawn the scanner subprocess for `(smb, sid)` combos. Tests mock `subprocess.run`.

The split lets each chunk be independently committable and testable.

---

## SAMR call sequence (reference)

For a user SID `S-1-5-21-D1-D2-D3-RID`:

1. `SamrConnect5(server_name, access_mask=MAXIMUM_ALLOWED)` → server handle
2. Split SID: domain SID = `S-1-5-21-D1-D2-D3`, user RID = `RID`
3. `SamrOpenDomain(server_handle, domain_sid, access_mask=MAXIMUM_ALLOWED)` → domain handle
4. `SamrOpenUser(domain_handle, user_rid, access_mask=READ)` → user handle
5. `SamrGetGroupsForUser(user_handle)` → array of `{rid, attributes}`
6. `SamrLookupIdsInDomain(domain_handle, [group_rids…])` → array of names
7. `SamrCloseHandle` for user handle, domain handle, server handle (in reverse order)

References:
- [MS-SAMR] §3.1.5 (server-side procedures), §3.1.5.1.4 (Connect5), §3.1.5.1.5 (OpenDomain), §3.1.5.1.9 (OpenUser), §3.1.5.9.1 (GetGroupsForUser), §3.1.5.11.2 (LookupIdsInDomain).
- SAMR interface UUID: `12345778-1234-ABCD-EF00-0123456789AC`, version 1.0 (per [MS-SAMR] §1.9).

---

## File structure

**Create**
- `scanner/internal/samr/bind.go` — SAMR UUID + `BuildBindRequest`.
- `scanner/internal/samr/pdu.go` — DCE/RPC PDU header (mirror of lsarpc).
- `scanner/internal/samr/ndr.go` — NDR helpers: SID encoding, RPC_SID, RPC_UNICODE_STRING (mirror), aligned UTF-16 strings, conformant arrays.
- `scanner/internal/samr/reader.go` — byte reader (mirror of lsarpc).
- `scanner/internal/samr/errors.go` — shared error sentinels.
- `scanner/internal/samr/handle.go` — `Handle [20]byte` plus helpers.
- `scanner/internal/samr/connect.go` — `BuildConnect5Request` / `ParseConnect5Response`.
- `scanner/internal/samr/open_domain.go` — `BuildOpenDomainRequest` / `ParseOpenDomainResponse`.
- `scanner/internal/samr/open_user.go` — `BuildOpenUserRequest` / `ParseOpenUserResponse`.
- `scanner/internal/samr/get_groups.go` — `BuildGetGroupsForUserRequest` / `ParseGetGroupsForUserResponse`.
- `scanner/internal/samr/lookup_ids.go` — `BuildLookupIdsInDomainRequest` / `ParseLookupIdsInDomainResponse`.
- `scanner/internal/samr/close.go` — `BuildSamrCloseHandleRequest`.
- `scanner/internal/samr/client.go` — `Client` wrapping a `Transport`, with `Bind`, `Connect`, `OpenDomain`, `OpenUser`, `GetGroupsForUser`, `LookupIds`, `CloseAll`.
- `scanner/internal/samr/sid.go` — SID parse/format helpers (`ParseSidString`, `SplitDomainAndRid`, etc.).
- `scanner/internal/samr/*_test.go` — per-file unit tests using hand-crafted byte fixtures.
- `scanner/internal/connector/smb_samr.go` — small adapter so SMBConnector can lazily mount IPC$, open the samr pipe, and run a resolution.
- `scanner/cmd/akashic-scanner/resolve_groups.go` — new subcommand.
- `api/tests/test_group_resolver_samr.py` — unit tests with mocked subprocess.

**Edit**
- `scanner/cmd/akashic-scanner/main.go` — dispatch to the new subcommand on `os.Args[1] == "resolve-groups"`.
- `api/akashic/services/group_resolver.py` — add `_resolve_smb_samr(source, binding)` and dispatcher wiring.
- `api/akashic/config.py` — add `scanner_binary_path: str = "/usr/local/bin/akashic-scanner"` (defaults to PATH lookup if unset).

**No deletes.**

---

## Cross-task spec: response shapes

`ResolveResult.source` Literal grows to include `"samr"`:
```python
class ResolveResult(BaseModel):
    groups: list[str]
    source: Literal["nss", "ldap", "ssh", "samr"]
    resolved_at: datetime
```

Scanner subcommand stdout JSON:
```json
{"groups": ["domain users", "engineers"], "source": "samr"}
```

Scanner subcommand stderr (on error): one line per failure step, e.g.:
```
samr: bind: SMB session: connection refused
```
Exit code: 0 success, 1 unrecoverable error, 2 not-found (user RID not in domain).

---

## Chunk 1 — SAMR protocol primitives

### Task 1.1 — Shared DCE/RPC scaffolding (mirror of lsarpc)

**Files:**
- Create: `scanner/internal/samr/pdu.go`, `reader.go`, `errors.go`.

These three are line-for-line copies of the lsarpc equivalents (license-compatible internal copies). Future cleanup can refactor them into a shared `scanner/internal/dcerpc/` package; not in scope here.

- [ ] **Step 1: Copy `pdu.go`** verbatim from `scanner/internal/lsarpc/pdu.go`, change `package lsarpc` → `package samr`. Keep PDU header constants and `Marshal`/`ParsePDUHeader`.

- [ ] **Step 2: Copy `reader.go`** from `scanner/internal/lsarpc/reader.go` with package rename. Drop `SkipDomains` (LSARPC-specific) — leave only the generic `U16`, `U32`, `Bytes`, `AlignTo`, `Tail32` helpers and `decodeUTF16`.

- [ ] **Step 3: Copy `errors.go`** from `scanner/internal/lsarpc/errors.go` with package rename. Add additional sentinels: `ErrSamrConnectFailed`, `ErrSamrOpenDomainFailed`, `ErrSamrOpenUserFailed`, `ErrSamrGetGroupsFailed`, `ErrSamrLookupIdsFailed`.

- [ ] **Step 4: Add tests** mirroring `pdu_test.go` and `reader_test.go` to verify the copies parse/serialize correctly.

**Verify:** `cd scanner && go build ./internal/samr/... && go test ./internal/samr/...` exits clean (currently empty package — should pass with no tests).

### Task 1.2 — SAMR-specific NDR

**File:** `scanner/internal/samr/ndr.go`

Contents:
```go
package samr

import (
    "encoding/binary"
    "unicode/utf16"
)

// EncodeUTF16LE returns the UTF-16 little-endian byte sequence for s,
// without a trailing null.
func EncodeUTF16LE(s string) []byte { /* same as lsarpc */ }

// Pad4 returns the number of zero-pad bytes needed to align n to 4 bytes.
func Pad4(n int) int { return (4 - n%4) % 4 }

// EncodeRPCUnicodeString — same as lsarpc.EncodeRPCUnicodeString.
// Used by Connect5 (server name) and OpenDomain.
func EncodeRPCUnicodeString(s string, referentID uint32) []byte { /* same */ }

// EncodeRPCSID encodes an RPC_SID structure (MS-DTYP §2.4.2.3) inline:
//   uint8  Revision     (1)
//   uint8  SubAuthCount
//   [6]byte IdentifierAuthority
//   [N]uint32 SubAuthority   (little-endian)
// The wire form for a referent-encoded RPC_SID is:
//   uint32 conformance count = SubAuthCount
//   inline RPC_SID structure
// (per [MS-SAMR] §2.2.2.4 SAMPR_RID_ENUMERATION + RPC_SID conformance handling).
func EncodeRPCSID(sid SID, referentID uint32) []byte { … }

// DecodeUTF16LE decodes a UTF-16LE byte slice into a Go string.
func DecodeUTF16LE(b []byte) string { /* same */ }
```

`SID` is the Go representation:
```go
type SID struct {
    Revision     uint8
    Authority    [6]byte
    SubAuthority []uint32
}
```

- [ ] **Step 1: Implement `EncodeRPCSID`**. The wire format starts with a `uint32` conformance count (number of sub-authorities), then the inline RPC_SID. Pad to 4-byte boundary.

- [ ] **Step 2: Test against MS-SAMR §4.5 fixture** — the example domain SID `S-1-5-21-1004336348-1177238915-682003330` should encode to a known byte sequence. Hand-verify against [MS-DTYP] §2.4.2.3.

  Expected bytes for `EncodeRPCSID(parseSidString("S-1-5-21-1004336348-1177238915-682003330"), 0x20000):`
  ```
  04 00 00 00                      // conformance count = 4
  01                               // revision = 1
  04                               // sub-auth count = 4
  00 00 00 00 00 05                // authority = NT (5)
  15 00 00 00                      // sub-auth[0] = 21
  9c bd 36 3b                      // sub-auth[1] = 1004336348 (LE)
  43 e9 30 46                      // sub-auth[2] = 1177238915 (LE)
  82 80 a3 28                      // sub-auth[3] = 682003330 (LE)
  ```

  *(Test fixture must match these bytes; this is the authoritative encoding.)*

### Task 1.3 — SID helpers

**File:** `scanner/internal/samr/sid.go`

- [ ] **Step 1: `ParseSidString`** — accepts canonical `S-1-5-21-…-RID` string form, returns `SID{}`.
- [ ] **Step 2: `SplitDomainAndRid`** — given a user SID, returns the domain SID (drop last sub-authority) and the RID (last sub-authority). Errors if sub-auth count < 1.
- [ ] **Step 3: Tests** — round-trip `S-1-5-21-1004336348-1177238915-682003330-1013` through both helpers; assert domain SID has 3 sub-auths and RID = 1013.

### Task 1.4 — SAMR bind

**File:** `scanner/internal/samr/bind.go`

Same as `lsarpc/bind.go` but with the SAMR UUID and version. The SAMR interface UUID is `12345778-1234-ABCD-EF00-0123456789AC` version 1.0.

- [ ] **Step 1: Define `samrUUID`, `samrVersion=1`, `samrVersionMinor=0`**. Note the byte-order — RPC binary GUIDs are little-endian per field, so the literal byte array is:
  ```go
  var samrUUID = [16]byte{
      0x78, 0x57, 0x34, 0x12, 0x34, 0x12, 0xcd, 0xab,
      0xef, 0x00, 0x01, 0x23, 0x45, 0x67, 0x89, 0xac,
  }
  ```

- [ ] **Step 2: Implement `BuildBindRequest`** — same wire structure as lsarpc, only the abstract syntax UUID/version differ.

- [ ] **Step 3: Test** — assert the bind PDU is exactly 72 bytes, starts with `05 00 0b 03 …`, and contains the SAMR UUID at the right offset.

**Verify chunk 1:** `go test ./internal/samr/...` exits clean. Coverage includes one test per file.

---

## Chunk 2 — SAMR opcodes

### Task 2.1 — `SamrConnect5` (opnum 64)

**File:** `scanner/internal/samr/connect.go`

Request body (per MS-SAMR §3.1.5.1.4):
- `[in, ptr] PSAMPR_SERVER_NAME` — RPC_UNICODE_STRING wrapped in a referent. Server name is typically `"\\\\hostname"` in UTF-16LE.
- `[in] DesiredAccess` — uint32, use `SAMR_SERVER_LOOKUP_DOMAIN | SAMR_SERVER_CONNECT = 0x21` for our use.
- `[in] InVersion` — uint32 = 1.
- `[in, switch_is(InVersion)] InRevisionInfo` — for InVersion=1: uint32 LengthOfBuffer + uint32 SupportedFeatures + ptr-to-buffer. Use empty (LengthOfBuffer=0, SupportedFeatures=0, buffer-ptr=null).

Response body:
- `[out] OutVersion` — uint32.
- `[out, switch_is(OutVersion)] OutRevisionInfo` — same structure as in.
- `[out] ServerHandle` — 20 bytes.
- `[out] return` — uint32 NTSTATUS.

- [ ] **Step 1: Build request** — encode UTF-16LE-wrapped RPC_UNICODE_STRING for server name with referent 0x00020000.
- [ ] **Step 2: Parse response** — read OutVersion, skip OutRevisionInfo (variable but for InVersion=1 = 12 bytes), read 20-byte handle, read NTSTATUS at tail.
- [ ] **Step 3: Tests** — encode known-input test, parse hand-crafted response with handle = 20 zero bytes + status 0.

### Task 2.2 — `SamrOpenDomain` (opnum 7)

**File:** `scanner/internal/samr/open_domain.go`

Request body:
- `[in] ServerHandle` — 20 bytes.
- `[in] DesiredAccess` — uint32. Use `0x205 = DOMAIN_LOOKUP | DOMAIN_LIST_ACCOUNTS`.
- `[in] DomainId` — RPC_SID encoded with EncodeRPCSID.

Response body:
- `[out] DomainHandle` — 20 bytes.
- `[out] return` — uint32 NTSTATUS.

- [ ] **Step 1: Build request** with server handle + access + domain SID.
- [ ] **Step 2: Parse response**.
- [ ] **Step 3: Tests** — fixture: domain SID encodes to the bytes from Task 1.2.

### Task 2.3 — `SamrOpenUser` (opnum 34)

**File:** `scanner/internal/samr/open_user.go`

Request body:
- `[in] DomainHandle` — 20 bytes.
- `[in] DesiredAccess` — uint32. Use `0x100 = USER_READ_GROUP_INFORMATION`.
- `[in] UserId` — uint32 RID.

Response: 20-byte handle + NTSTATUS.

- [ ] Standard build/parse/test trio.

### Task 2.4 — `SamrGetGroupsForUser` (opnum 39)

**File:** `scanner/internal/samr/get_groups.go`

Request body:
- `[in] UserHandle` — 20 bytes.

Response body:
- `[out, ptr] PSAMPR_GET_GROUPS_BUFFER` — referent + count + ptr-to-array of `{uint32 rid, uint32 attributes}` (count entries) + tail NTSTATUS.

- [ ] **Step 1: Build request** — just the user handle.
- [ ] **Step 2: Parse response** — read referent (skip if 0), count, conformance, then `count` (rid, attributes) pairs, then NTSTATUS.
- [ ] **Step 3: Tests** — fixture with 2 group RIDs.

### Task 2.5 — `SamrLookupIdsInDomain` (opnum 18)

**File:** `scanner/internal/samr/lookup_ids.go`

Request body:
- `[in] DomainHandle` — 20 bytes.
- `[in] Count` — uint32.
- `[in, size_is(1000), length_is(Count)] Rids[]` — uint32 max=1000, length=Count, then `Count` uint32 RIDs.

Response body:
- `[out] Names` — SAMPR_RETURNED_USTRING_ARRAY: referent + count + max + per-entry RPC_UNICODE_STRING entries (deferred names follow).
- `[out] Use` — SAMPR_ULONG_ARRAY: referent + count + per-entry uint32 (sid_type per RID).
- `[out] return` — NTSTATUS.

- [ ] **Step 1: Build request** with conformance markers (max=1000, offset=0, length=Count).
- [ ] **Step 2: Parse response** — gather only the names (we don't need use[] for our flow). Decode each RPC_UNICODE_STRING.
- [ ] **Step 3: Tests** — fixture with 2 names.

### Task 2.6 — `SamrCloseHandle` (opnum 1)

**File:** `scanner/internal/samr/close.go`

Request body: 20-byte handle.
Response body: 20-byte handle (zeroed) + NTSTATUS.

- [ ] Trivial; mirror `lsarpc.BuildLsarCloseRequest`.

**Verify chunk 2:** all opcode tests pass; `go test ./internal/samr/...` exits clean.

---

## Chunk 3 — SAMR client wrapper

### Task 3.1 — Client wrapper

**File:** `scanner/internal/samr/client.go`

```go
type Transport interface {
    io.ReadWriteCloser
}

type Client struct {
    t            Transport
    callID       uint32
    serverHandle Handle
    domainHandle Handle
    userHandle   Handle
    bound        bool
}

func NewClient(t Transport) *Client { return &Client{t: t, callID: 1} }

func (c *Client) Bind() error                            // builds Bind PDU, expects BindAck
func (c *Client) Connect(serverName string) error        // SamrConnect5 → serverHandle
func (c *Client) OpenDomain(domain SID) error            // SamrOpenDomain → domainHandle
func (c *Client) OpenUser(rid uint32) error              // SamrOpenUser → userHandle
func (c *Client) GetGroupsForUser() ([]uint32, error)    // → group RIDs
func (c *Client) LookupIds(rids []uint32) ([]string, error)  // → group names
func (c *Client) Close()                                 // best-effort SamrCloseHandle for each open handle, then transport
```

- [ ] **Step 1: Implement** following the Phase 9 LSARPC client pattern verbatim — it's the same orchestration, just different opcodes.

- [ ] **Step 2: Tests** — `client_test.go` with a mockTransport that drains writes and returns canned responses for each opcode in sequence. End-to-end: feed Bind→Connect→OpenDomain→OpenUser→GetGroups→LookupIds and assert the final group-name list.

### Task 3.2 — `ResolveGroupsForSid` helper

**File:** `scanner/internal/samr/resolve.go`

```go
// ResolveGroupsForSid runs the full SAMR sequence:
//   Bind → Connect5 → OpenDomain(domainSid) → OpenUser(rid) →
//   GetGroupsForUser → LookupIds → Close.
//
// Returns the list of group names. server is the host string used in
// SamrConnect5 (must start with "\\\\"). userSid is the full user SID;
// it's split into domain SID + RID internally.
func ResolveGroupsForSid(t Transport, server string, userSid SID) ([]string, error) {
    domain, rid, err := SplitDomainAndRid(userSid)
    if err != nil { return nil, err }
    c := NewClient(t)
    defer c.Close()
    if err := c.Bind(); err != nil { return nil, fmt.Errorf("bind: %w", err) }
    if err := c.Connect(server); err != nil { return nil, fmt.Errorf("connect: %w", err) }
    if err := c.OpenDomain(domain); err != nil { return nil, fmt.Errorf("open_domain: %w", err) }
    if err := c.OpenUser(rid); err != nil { return nil, fmt.Errorf("open_user: %w", err) }
    rids, err := c.GetGroupsForUser()
    if err != nil { return nil, fmt.Errorf("get_groups: %w", err) }
    if len(rids) == 0 { return nil, nil }
    return c.LookupIds(rids)
}
```

- [ ] Implement and add an end-to-end test using the chained mockTransport from Task 3.1.

**Verify chunk 3:** `go test ./internal/samr/... -run TestResolveGroupsForSid` passes.

---

## Chunk 4 — Scanner CLI subcommand

### Task 4.1 — `resolve-groups` subcommand wiring

**File:** `scanner/cmd/akashic-scanner/resolve_groups.go`

```go
func runResolveGroups(args []string) {
    fs := flag.NewFlagSet("resolve-groups", flag.ExitOnError)
    sourceType := fs.String("type", "", "Source type (smb)")
    host := fs.String("host", "", "SMB host")
    user := fs.String("user", "", "SMB username")
    password := fs.String("password", "", "SMB password")
    sidStr := fs.String("sid", "", "User SID to resolve groups for")
    _ = fs.Parse(args)

    if *sourceType != "smb" {
        fmt.Fprintln(os.Stderr, "resolve-groups: only --type=smb is supported")
        os.Exit(1)
    }
    sid, err := samr.ParseSidString(*sidStr)
    if err != nil {
        fmt.Fprintf(os.Stderr, "resolve-groups: bad sid: %v\n", err)
        os.Exit(1)
    }

    transport, err := openSamrPipe(*host, *user, *password)
    if err != nil {
        fmt.Fprintf(os.Stderr, "resolve-groups: pipe: %v\n", err)
        os.Exit(1)
    }
    defer transport.Close()

    server := fmt.Sprintf("\\\\%s", *host)
    groups, err := samr.ResolveGroupsForSid(transport, server, sid)
    if err != nil {
        // Map structural errors. We use exit code 2 for "not found" so
        // the API caller can disambiguate.
        if errors.Is(err, samr.ErrSamrOpenUserFailed) {
            fmt.Fprintf(os.Stderr, "resolve-groups: user not found in domain\n")
            os.Exit(2)
        }
        fmt.Fprintf(os.Stderr, "resolve-groups: %v\n", err)
        os.Exit(1)
    }

    out := struct {
        Groups []string `json:"groups"`
        Source string   `json:"source"`
    }{Groups: groups, Source: "samr"}
    json.NewEncoder(os.Stdout).Encode(out)
}
```

The `openSamrPipe` helper does the SMB session + IPC$ mount + `OpenFile("samr", ...)` — same dance the SMBConnector does in `connector/smb.go` for LSARPC. Extract it into `connector/smb_samr.go` so both can call it.

- [ ] **Step 1:** Create `scanner/internal/connector/smb_samr.go` with `OpenSamrPipe(host, user, password string) (io.ReadWriteCloser, error)` returning a transport bound to the closed-over SMB session/IPC$/file (Closes them all on transport.Close).

- [ ] **Step 2:** Wire `cmd/akashic-scanner/main.go` so that if `os.Args[1] == "resolve-groups"`, it calls `runResolveGroups(os.Args[2:])` and returns. Existing flags only apply when no subcommand is present.

- [ ] **Step 3:** Manual smoke against any reachable Windows/Samba host:
  ```
  ./akashic-scanner resolve-groups --type=smb --host=$SMB_HOST --user=$SMB_USER --password=$SMB_PW --sid=$USER_SID
  ```
  Expected: JSON object with groups list. (If no test host available, document this as deferred to deployment-time validation.)

**Verify chunk 4:** binary builds; subcommand prints helpful error on missing flags.

---

## Chunk 5 — API integration

### Task 5.1 — `_resolve_smb_samr` helper

**File:** `api/akashic/services/group_resolver.py`

Add:

```python
import json
import shutil
import subprocess

_SCANNER_BIN_ENV = "AKASHIC_SCANNER_BIN"


def _scanner_binary_path() -> str | None:
    """Returns the path to the akashic-scanner binary, or None if not on PATH."""
    p = os.environ.get(_SCANNER_BIN_ENV)
    if p and os.path.isfile(p):
        return p
    return shutil.which("akashic-scanner")


def _resolve_smb_samr(source, binding) -> ResolveResult:
    """Resolve groups for an NT SID against an SMB source by invoking the
    `akashic-scanner resolve-groups` subcommand. The Go process opens a
    DCE/RPC connection over SMB IPC$ to the \\\\PIPE\\samr endpoint."""
    cfg = source.connection_config or {}
    host = cfg.get("host")
    if not host:
        raise UnsupportedResolution("Source missing host in connection_config")
    username = cfg.get("username")
    if not username:
        raise UnsupportedResolution("Source missing username in connection_config")
    password = cfg.get("password") or ""

    sid = (binding.identifier or "").strip()
    if not sid.startswith("S-1-"):
        raise ResolutionFailed("not_found", f"identifier {sid!r} is not a SID")

    binary = _scanner_binary_path()
    if not binary:
        raise UnsupportedResolution(
            "akashic-scanner binary not found on PATH; set AKASHIC_SCANNER_BIN"
        )

    try:
        proc = subprocess.run(
            [
                binary, "resolve-groups",
                "--type=smb", "--host", host,
                "--user", username, "--password", password,
                "--sid", sid,
            ],
            capture_output=True, timeout=30, text=True,
        )
    except subprocess.TimeoutExpired:
        raise ResolutionFailed("backend_error", "scanner timeout")
    except OSError as exc:
        raise ResolutionFailed("backend_error", f"scanner spawn: {exc}")

    if proc.returncode == 2:
        raise ResolutionFailed("not_found", proc.stderr.strip() or "user not found in domain")
    if proc.returncode != 0:
        raise ResolutionFailed("backend_error", proc.stderr.strip() or "scanner failed")

    try:
        payload = json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        raise ResolutionFailed("backend_error", f"scanner output not JSON: {exc}")

    return ResolveResult(
        groups=payload.get("groups", []) or [],
        source="samr",
        resolved_at=datetime.now(timezone.utc),
    )
```

Update the dispatcher:
```python
    if id_type == "sid":
        if src_type == "smb":
            return _resolve_smb_samr(source, binding)
        raise UnsupportedResolution(
            f"sid resolution not supported on source.type={src_type!r}"
        )
```

Update `ResolveResult.source` Literal: `"nss" | "ldap" | "ssh" | "samr"`.

- [ ] **Step 1:** Add the helper, wire the dispatcher, update the Literal.
- [ ] **Step 2:** Update `api/akashic/services/group_resolver.py` module docstring to drop the "Phase 14c will add NT SMB" line.

### Task 5.2 — Tests

**File:** `api/tests/test_group_resolver_samr.py`

Mock `subprocess.run` to return a `CompletedProcess`. Cover:
- Happy path → groups list with `source="samr"`.
- Non-zero exit → `ResolutionFailed("backend_error")`.
- Exit code 2 → `ResolutionFailed("not_found")`.
- Bad JSON output → `ResolutionFailed("backend_error")`.
- Missing binary → `UnsupportedResolution`.
- Non-SID identifier → `ResolutionFailed("not_found")`.
- Missing host → `UnsupportedResolution`.
- Subprocess timeout → `ResolutionFailed("backend_error")`.

- [ ] **Step 1:** Write 8 tests using `monkeypatch.setattr` on `subprocess.run` and `_scanner_binary_path`.
- [ ] **Step 2:** Update Phase 14a `test_smb_unsupported` to assert that the dispatcher reaches `_resolve_smb_samr` (binary missing → UnsupportedResolution).

### Task 5.3 — Update Phase 14a SMB test

The existing `test_smb_unsupported` in `tests/test_group_resolver.py` asserts SMB sources raise `UnsupportedResolution`. After this phase, the dispatch reaches `_resolve_smb_samr`; without `AKASHIC_SCANNER_BIN` and no binary on PATH the helper still raises `UnsupportedResolution`, so the test should still pass — but rename it to clarify the new reason. Same approach Phase 14b used for `test_ssh_empty_config_unsupported`.

---

## Verification (end-to-end)

1. `cd scanner && go build ./... && go test ./internal/samr/...` — all SAMR tests pass.
2. `go test ./...` in scanner root — no other regressions.
3. Build image: `docker compose -f docker-compose.eff6.yml build api scanner` (if scanner has a service, otherwise just `go build`).
4. `docker compose -f docker-compose.eff6.yml run --rm api pytest tests/test_group_resolver_samr.py -v` — 8 passes.
5. `docker compose -f docker-compose.eff6.yml run --rm api pytest tests/test_group_resolver.py tests/test_group_resolver_ssh.py tests/test_group_resolver_samr.py` — full resolver suite passes.
6. **Live smoke (deferred):** point the scanner CLI at a real Windows or Samba host with a known user SID; expect group names. If no host is available in the test environment, document this as a manual deploy-time check.

---

## Out of scope (genuinely unbounded)

- Kerberos auth (the SMB client currently uses NTLM).
- Trusted domains / cross-forest lookups.
- Group nesting expansion (we return only direct memberships, same as POSIX `id -Gn`).
- Caching at the scanner level (the API-level `principal_groups_cache` from Phase 14a still applies).
- Live SAMR fixture replays — fixtures here are hand-crafted; full integration testing requires a real DC.
