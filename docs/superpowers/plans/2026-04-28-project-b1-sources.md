# Phase B1 — Self-Service Source Creation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Replace the hardcoded local-only "Add a source" form on `/sources` with a typed form supporting all five connector types (local, ssh, smb, nfs, s3), plus an optional pre-save "Test connection" button that exercises the credentials before persisting.

**Out of scope (deferred to later phases):** Editing existing sources, removing the modal in favor of a dedicated page, source health monitoring background checks, S3 bucket browser for picking buckets.

---

## Architecture

The form lives client-side. The "Save" button calls the existing `POST /api/sources`. The new "Test connection" button calls a new `POST /api/sources/test` endpoint that:

- For `local` and `nfs` paths the API can reach: `os.path.isdir()` check.
- For `ssh`/`smb`/`s3`: spawns `akashic-scanner test-connection --type=… --host=… …` as a subprocess, with the password fed via stdin JSON (same pattern as Phase 14c). The scanner attempts a connection + list of root entries, exits 0 on success, 1 with structured stderr (`step:reason`) on failure.

Bundling the scanner binary into the api image is a prerequisite. The current api Dockerfile is single-stage Python; we add a multi-stage build that compiles the Go scanner and copies it into the final image.

Frontend uses the existing `Select`, `Input`, `Button`, `Card`, `Drawer` primitives. Type picker drives a per-type field component; the form's payload is type-specific and posted as `connection_config`.

---

## File structure

**Create**

- `api/akashic/routers/source_test.py` — `POST /api/sources/test` endpoint.
- `api/akashic/services/source_tester.py` — dispatches to local probe, scanner subprocess, etc.
- `api/tests/test_source_test_endpoint.py` — endpoint tests with mocked subprocess.
- `scanner/cmd/akashic-scanner/test_connection.go` — new subcommand.
- `web/src/components/sources/AddSourceForm.tsx` — typed form replacing the inline one in Sources.tsx.
- `web/src/components/sources/source-fields/LocalFields.tsx`
- `web/src/components/sources/source-fields/NfsFields.tsx`
- `web/src/components/sources/source-fields/SshFields.tsx`
- `web/src/components/sources/source-fields/SmbFields.tsx`
- `web/src/components/sources/source-fields/S3Fields.tsx`
- `web/src/components/sources/sourceTypes.ts` — shared type defs + payload helpers.
- `web/src/hooks/useTestSource.ts` — useMutation for the new endpoint.

**Edit**

- `api/Dockerfile` — multi-stage: golang builder + scanner binary copied into the python image.
- `api/akashic/main.py` — register `source_test` router.
- `scanner/cmd/akashic-scanner/main.go` — dispatch `test-connection` subcommand.
- `web/src/pages/Sources.tsx` — drop inline `AddSourceForm`; mount the new typed `<AddSourceForm/>` from `components/sources/`.
- `web/src/types/index.ts` — add `SourceType` union and `TestSourceResult` shape.

**Delete**

- The inline `AddSourceForm` function inside `Sources.tsx` (replaced by import).

---

## Cross-task spec: payload + response shapes

`SourceType = "local" | "ssh" | "smb" | "nfs" | "s3"`

`connection_config` per type:

```ts
type LocalConfig = { path: string };

type NfsConfig = {
  host: string;
  export_path: string;
  mount_options?: string;  // advanced, optional
};

type SshConfig = {
  host: string;
  port?: number;             // default 22
  username: string;
  auth: "password" | "key";
  password?: string;         // when auth=password
  key_path?: string;         // when auth=key
  key_passphrase?: string;   // optional, when auth=key
  known_hosts_path: string;  // REQUIRED — strict by default
};

type SmbConfig = {
  host: string;
  port?: number;             // default 445
  username: string;
  password: string;
  share: string;
  domain?: string;
};

type S3Config = {
  endpoint?: string;         // optional, for non-AWS
  bucket: string;
  region: string;
  access_key_id: string;
  secret_access_key: string;
};
```

`POST /api/sources/test` request body: `{ type: SourceType, connection_config: { … } }` (the payload that would be posted to `POST /api/sources`, minus `name`).

Response (always 200; success encoded in body):

```json
{ "ok": true,  "step": null, "error": null }
{ "ok": false, "step": "auth", "error": "NT_STATUS_LOGON_FAILURE" }
```

`step` enum: `"connect" | "auth" | "mount" | "list" | "config" | null`.

**Audit:** existing `source_created` event already fires from `POST /api/sources`. We add a new `source_test_run` event type emitted by the test endpoint with `{type, host, ok, step, error}` — never the password/secret_access_key.

---

## Task 1 — Bundle scanner binary into api image

**Files:**
- Modify: `api/Dockerfile`

Currently single-stage Python. Convert to multi-stage:

```dockerfile
# ---- builder: compile the Go scanner ----
FROM golang:1.23 AS scanner-builder
WORKDIR /src
COPY scanner/go.mod scanner/go.sum ./scanner/
RUN cd scanner && go mod download
COPY scanner/ ./scanner/
RUN cd scanner && CGO_ENABLED=0 go build -o /out/akashic-scanner ./cmd/akashic-scanner

# ---- runtime: python + bundled scanner ----
FROM python:3.12-slim
WORKDIR /app

COPY api/pyproject.toml .
COPY api/akashic/ akashic/
COPY api/alembic.ini .
COPY api/alembic/ alembic/

RUN apt-get update && apt-get install -y gcc libldap2-dev libsasl2-dev && rm -rf /var/lib/apt/lists/*
RUN pip install --no-cache-dir .

COPY --from=scanner-builder /out/akashic-scanner /usr/local/bin/akashic-scanner

CMD ["uvicorn", "akashic.main:app", "--host", "0.0.0.0", "--port", "8000"]
```

The build context needs to be the **repo root**, not `./api`. Update `compose.yaml`:

```yaml
api:
  build:
    context: .
    dockerfile: api/Dockerfile
```

- [ ] **Step 1**: Rewrite `api/Dockerfile` per above.
- [ ] **Step 2**: Update `compose.yaml` `api.build.context: .` and `dockerfile: api/Dockerfile`.
- [ ] **Step 3**: Update each worktree compose file pattern (none ship in main; this is just for local dev consistency).
- [ ] **Step 4**: Verify `docker compose build api` succeeds and `docker compose run --rm api which akashic-scanner` prints `/usr/local/bin/akashic-scanner`.

**Verify:** the binary exists in the image and `akashic-scanner --help` exits 0.

---

## Task 2 — Scanner CLI test-connection subcommand

**Files:**
- Create: `scanner/cmd/akashic-scanner/test_connection.go`
- Modify: `scanner/cmd/akashic-scanner/main.go`

Surface:

```
akashic-scanner test-connection --type=ssh --host=h --user=u --port=22 \
    --known-hosts=/etc/ssh/known_hosts --key=/path/to/key --password-stdin \
    [--share=s] [--bucket=b] [--region=r] [--endpoint=e]
```

Reads password from stdin JSON `{"password":"…"}` when `--password-stdin` is set.

Logic per type:

- **ssh**: dial → ssh handshake with provided auth → request a single SFTP session → ListDir("/") with timeout 5s → close.
- **smb**: dial → SMB negotiate + NTLM auth → mount the share → ReadDir at root → unmount → close.
- **s3**: build an S3 client with the provided creds + region/endpoint → call `HeadBucket`. (No need to list — HeadBucket is sufficient and respects bucket-policy denies cleanly.)
- **nfs**: dial via the Go nfs-client (if scanner already vendors one — if not, fall back to `os.Stat` of the export path remotely via mount-then-stat. Defer to whatever the connector already does.) — see step 3 if nfs is structurally harder than ssh/smb/s3.
- **local**: no scanner involvement; the API does this directly.

Output JSON to stdout on success: `{"ok":true}`. On failure, exit 1 with stderr `step:reason` one-liner: `connect:dial tcp …: connection refused` or `auth:NT_STATUS_LOGON_FAILURE` or `list:permission denied`. The API parses this.

- [ ] **Step 1**: Create `scanner/cmd/akashic-scanner/test_connection.go` with one function per supported type.
- [ ] **Step 2**: Modify `main.go` to dispatch `test-connection` similarly to `resolve-groups`.
- [ ] **Step 3**: Decide on NFS scope — if the scanner already has an NFS connector with a "ping" path, reuse it; otherwise emit `step=config error="NFS test not yet supported (Phase B1.1)"` and accept that NFS is Save-only for now. Document in the spec.
- [ ] **Step 4**: Tests in `scanner/cmd/akashic-scanner/test_connection_test.go` for the JSON output shape on simulated success/failure (mock the connector via interfaces).

**Verify:**
- `akashic-scanner test-connection --type=smb --host=does-not-exist --user=u --share=s --password-stdin <<< '{"password":"x"}'` exits 1 with stderr containing `connect:`.
- Build succeeds in the multi-stage image from Task 1.

---

## Task 3 — API source_test service + endpoint

**Files:**
- Create: `api/akashic/services/source_tester.py`
- Create: `api/akashic/routers/source_test.py`
- Modify: `api/akashic/main.py`

**`source_tester.py`:**

```python
"""Test-connection probes for source creation. Local/NFS hit the API
container's filesystem directly; SSH/SMB/S3 dispatch to the akashic-scanner
test-connection subcommand."""
import json
import os
import shutil
import subprocess
from typing import Literal, Optional

from pydantic import BaseModel

Step = Literal["connect", "auth", "mount", "list", "config"]


class TestResult(BaseModel):
    ok: bool
    step: Optional[Step] = None
    error: Optional[str] = None


_SCANNER_BIN_ENV = "AKASHIC_SCANNER_BIN"


def _scanner_binary_path() -> str | None:
    p = os.environ.get(_SCANNER_BIN_ENV)
    if p and os.path.isfile(p):
        return p
    return shutil.which("akashic-scanner")


def _run_scanner(argv: list[str], password: str = "", timeout: int = 15) -> subprocess.CompletedProcess:
    payload = json.dumps({"password": password}) + "\n"
    return subprocess.run(
        argv, capture_output=True, timeout=timeout, text=True, input=payload,
    )


def test_local(cfg: dict) -> TestResult:
    path = (cfg.get("path") or "").strip()
    if not path:
        return TestResult(ok=False, step="config", error="path required")
    if not os.path.isdir(path):
        return TestResult(ok=False, step="list", error=f"not a directory: {path}")
    if not os.access(path, os.R_OK):
        return TestResult(ok=False, step="list", error=f"not readable: {path}")
    return TestResult(ok=True)


def _test_via_scanner(scanner_argv: list[str], password: str = "") -> TestResult:
    binary = _scanner_binary_path()
    if not binary:
        return TestResult(ok=False, step="config",
                          error="akashic-scanner binary not found")
    argv = [binary] + scanner_argv
    try:
        proc = _run_scanner(argv, password=password)
    except subprocess.TimeoutExpired:
        return TestResult(ok=False, step="connect", error="timeout")
    except OSError as exc:
        return TestResult(ok=False, step="config", error=f"scanner spawn: {exc}")

    if proc.returncode == 0:
        return TestResult(ok=True)

    err = (proc.stderr or "").strip()
    step: Step | None = None
    if ":" in err:
        prefix, _, msg = err.partition(":")
        if prefix in ("connect", "auth", "mount", "list", "config"):
            step = prefix  # type: ignore[assignment]
            err = msg.strip()
    return TestResult(ok=False, step=step, error=err)


def test_ssh(cfg: dict) -> TestResult:
    host = (cfg.get("host") or "").strip()
    user = (cfg.get("username") or "").strip()
    if not host or not user:
        return TestResult(ok=False, step="config", error="host and username required")
    if not (cfg.get("known_hosts_path") or "").strip():
        return TestResult(ok=False, step="config", error="known_hosts_path required")
    argv = ["test-connection", "--type=ssh",
            "--host", host,
            "--port", str(cfg.get("port") or 22),
            "--user", user,
            "--known-hosts", cfg["known_hosts_path"],
            "--password-stdin"]
    if cfg.get("key_path"):
        argv += ["--key", cfg["key_path"]]
        if cfg.get("key_passphrase"):
            argv += ["--key-passphrase", cfg["key_passphrase"]]
    return _test_via_scanner(argv, password=cfg.get("password") or "")


def test_smb(cfg: dict) -> TestResult:
    host = (cfg.get("host") or "").strip()
    user = (cfg.get("username") or "").strip()
    share = (cfg.get("share") or "").strip()
    if not host or not user or not share:
        return TestResult(ok=False, step="config",
                          error="host, username, share required")
    argv = ["test-connection", "--type=smb",
            "--host", host,
            "--port", str(cfg.get("port") or 445),
            "--user", user,
            "--share", share,
            "--password-stdin"]
    return _test_via_scanner(argv, password=cfg.get("password") or "")


def test_s3(cfg: dict) -> TestResult:
    bucket = (cfg.get("bucket") or "").strip()
    region = (cfg.get("region") or "").strip()
    if not bucket or not region:
        return TestResult(ok=False, step="config",
                          error="bucket and region required")
    argv = ["test-connection", "--type=s3",
            "--bucket", bucket,
            "--region", region]
    if cfg.get("endpoint"):
        argv += ["--endpoint", cfg["endpoint"]]
    if cfg.get("access_key_id"):
        argv += ["--user", cfg["access_key_id"]]
    return _test_via_scanner(argv, password=cfg.get("secret_access_key") or "")


def test_nfs(cfg: dict) -> TestResult:
    # Phase B1: NFS test is not yet implemented in the scanner. Save still
    # works; user just doesn't get pre-flight validation.
    if not (cfg.get("host") or "").strip() or not (cfg.get("export_path") or "").strip():
        return TestResult(ok=False, step="config",
                          error="host and export_path required")
    return TestResult(ok=False, step="config",
                      error="NFS connection test not yet supported")


_DISPATCH = {
    "local": test_local,
    "ssh":   test_ssh,
    "smb":   test_smb,
    "s3":    test_s3,
    "nfs":   test_nfs,
}


def test_connection(source_type: str, connection_config: dict) -> TestResult:
    fn = _DISPATCH.get(source_type)
    if fn is None:
        return TestResult(ok=False, step="config",
                          error=f"unsupported source type: {source_type}")
    return fn(connection_config or {})
```

**`routers/source_test.py`:**

```python
from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from akashic.auth.dependencies import get_current_user
from akashic.database import get_db
from akashic.models.user import User
from akashic.services.audit import record_event
from akashic.services.source_tester import test_connection, TestResult


router = APIRouter(prefix="/api/sources", tags=["sources"])


class TestSourceRequest(BaseModel):
    type: str
    connection_config: dict


@router.post("/test", response_model=TestResult)
async def post_test(
    body: TestSourceRequest,
    request: Request = None,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    # Run synchronously — the test itself is short (≤15s scanner timeout).
    result = test_connection(body.type, body.connection_config)
    await record_event(
        db=db, user=user,
        event_type="source_test_run",
        payload={
            "type": body.type,
            "host": (body.connection_config or {}).get("host"),
            "bucket": (body.connection_config or {}).get("bucket"),
            "ok": result.ok,
            "step": result.step,
            "error": result.error,
        },
        request=request,
    )
    return result
```

- [ ] **Step 1**: Create the service.
- [ ] **Step 2**: Create the router.
- [ ] **Step 3**: Register the router in `api/akashic/main.py`.
- [ ] **Step 4**: Tests in `api/tests/test_source_test_endpoint.py`:
  - Local with valid path → `{ok:true}`.
  - Local with non-existent path → `{ok:false, step:"list"}`.
  - Local with config-empty → `{ok:false, step:"config"}`.
  - SSH with mocked `_run_scanner` returning `(rc=0)` → ok.
  - SSH with mocked `_run_scanner` returning `(rc=1, stderr="auth:bad password")` → `{ok:false, step:"auth", error:"bad password"}`.
  - Audit event recorded for both pass and fail; payload contains type+host but never password/secret_access_key.

**Verify:** `pytest api/tests/test_source_test_endpoint.py -v` → all pass.

---

## Task 4 — Frontend types + per-type field components

**Files:**
- Create: `web/src/components/sources/sourceTypes.ts`
- Create: 5 field component files in `web/src/components/sources/source-fields/`.

**`sourceTypes.ts`:**

```ts
export const SOURCE_TYPES = ["local", "ssh", "smb", "nfs", "s3"] as const;
export type SourceType = (typeof SOURCE_TYPES)[number];

export const SOURCE_TYPE_LABELS: Record<SourceType, string> = {
  local: "Local filesystem",
  ssh:   "SSH / SFTP",
  smb:   "SMB / CIFS",
  nfs:   "NFS",
  s3:    "S3-compatible",
};

export type LocalConfig = { path: string };
export type NfsConfig = { host: string; export_path: string; mount_options?: string };
export type SshConfig = {
  host: string; port?: number; username: string;
  auth: "password" | "key";
  password?: string;
  key_path?: string; key_passphrase?: string;
  known_hosts_path: string;
};
export type SmbConfig = {
  host: string; port?: number; username: string; password: string;
  share: string; domain?: string;
};
export type S3Config = {
  endpoint?: string; bucket: string; region: string;
  access_key_id: string; secret_access_key: string;
};

export type AnyConfig = LocalConfig | NfsConfig | SshConfig | SmbConfig | S3Config;

export interface FieldsProps<C> {
  value: Partial<C>;
  onChange: (next: Partial<C>) => void;
}
```

Each field component renders the right `<Input>` / `<Select>` / `<RadioGroup>` for its type and lifts state via `onChange`.

- [ ] **Step 1**: `LocalFields.tsx` — single Path input.
- [ ] **Step 2**: `NfsFields.tsx` — Host, Export path, Mount options (collapsible advanced).
- [ ] **Step 3**: `SshFields.tsx` — Host, Port, Username, Auth method radio, conditional Password OR Key path + Passphrase, Known hosts path.
- [ ] **Step 4**: `SmbFields.tsx` — Host, Port, Username, Password, Share, Domain.
- [ ] **Step 5**: `S3Fields.tsx` — Endpoint (optional), Bucket, Region, Access key ID, Secret access key.

**Verify:** Each component renders standalone (Storybook-style smoke); types match the discriminated union.

---

## Task 5 — AddSourceForm component

**Files:**
- Create: `web/src/components/sources/AddSourceForm.tsx`
- Create: `web/src/hooks/useTestSource.ts`

**`useTestSource.ts`:**

```ts
import { useMutation } from "@tanstack/react-query";
import { api } from "../api/client";
import type { SourceType } from "../components/sources/sourceTypes";

export interface TestSourceResult {
  ok: boolean;
  step: "connect" | "auth" | "mount" | "list" | "config" | null;
  error: string | null;
}

export function useTestSource() {
  return useMutation({
    mutationFn: (data: { type: SourceType; connection_config: Record<string, unknown> }) =>
      api.post<TestSourceResult>("/sources/test", data),
  });
}
```

**`AddSourceForm.tsx`** (skeleton):

```tsx
export function AddSourceForm({ onCreated }: { onCreated?: () => void }) {
  const createSource = useCreateSource();
  const testSource  = useTestSource();
  const [name, setName] = useState("");
  const [type, setType] = useState<SourceType>("local");
  const [config, setConfig] = useState<Partial<AnyConfig>>({});
  const [formError, setFormError] = useState<string | null>(null);
  const [testResult, setTestResult] = useState<TestSourceResult | null>(null);

  // Reset config when type changes (different fields per type).
  useEffect(() => { setConfig({}); setTestResult(null); }, [type]);

  function renderFields() {
    switch (type) {
      case "local": return <LocalFields value={config as any} onChange={setConfig as any}/>;
      case "ssh":   return <SshFields   value={config as any} onChange={setConfig as any}/>;
      case "smb":   return <SmbFields   value={config as any} onChange={setConfig as any}/>;
      case "nfs":   return <NfsFields   value={config as any} onChange={setConfig as any}/>;
      case "s3":    return <S3Fields    value={config as any} onChange={setConfig as any}/>;
    }
  }

  async function handleTest() {
    setTestResult(null);
    try {
      const r = await testSource.mutateAsync({ type, connection_config: config as Record<string, unknown> });
      setTestResult(r);
    } catch (err) {
      setTestResult({ ok: false, step: null, error: err instanceof Error ? err.message : "Test failed" });
    }
  }

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setFormError(null);
    try {
      await createSource.mutateAsync({ name, type, connection_config: config as Record<string, unknown> });
      setName(""); setConfig({}); setTestResult(null); onCreated?.();
    } catch (err) {
      setFormError(err instanceof Error ? err.message : "Failed to create source");
    }
  }

  return (
    <Card padding="md">
      <CardHeader title="Add a source" />
      <form onSubmit={handleSubmit} className="space-y-3">
        <Input label="Name" value={name} onChange={(e) => setName(e.target.value)} required />
        <Select label="Type" value={type} onChange={(e) => setType(e.target.value as SourceType)}>
          {SOURCE_TYPES.map((t) => <option key={t} value={t}>{SOURCE_TYPE_LABELS[t]}</option>)}
        </Select>
        {renderFields()}
        {testResult && (
          <div className={`rounded-md p-2 text-xs ${testResult.ok ? "bg-emerald-50 text-emerald-800" : "bg-rose-50 text-rose-800"}`}>
            {testResult.ok ? "Connection OK" : `${testResult.step ?? "error"}: ${testResult.error}`}
          </div>
        )}
        {formError && <p className="text-xs text-rose-600">{formError}</p>}
        <div className="flex gap-2">
          <Button type="button" variant="secondary" onClick={handleTest} loading={testSource.isPending}>
            Test connection
          </Button>
          <Button type="submit" loading={createSource.isPending} className="flex-1">
            Add source
          </Button>
        </div>
      </form>
    </Card>
  );
}
```

- [ ] **Step 1**: Implement the component per skeleton.
- [ ] **Step 2**: Implement `useTestSource` hook.
- [ ] **Step 3**: Per-type config validation client-side (e.g., SSH `known_hosts_path` required); disable Save until valid.

---

## Task 6 — Wire into Sources.tsx + smoke test all 5 types

**Files:**
- Modify: `web/src/pages/Sources.tsx`

Replace the inline `AddSourceForm` function with an import:

```tsx
import { AddSourceForm } from "../components/sources/AddSourceForm";
```

Drop the inline definition entirely.

- [ ] **Step 1**: Edit `Sources.tsx`.
- [ ] **Step 2**: Smoke test in browser:
  - Local: existing path → save → appears in list. Bad path → Test connection shows "list:not a directory".
  - SSH: pick a reachable host (or use a Docker `linuxserver/openssh-server` quick stand-up) → Test → ok → save.
  - SMB: needs a Samba target; if no test target available, document as deployment-time validation.
  - NFS: save works, test returns "config: NFS connection test not yet supported".
  - S3: against MinIO container or AWS → Test → save.
- [ ] **Step 3**: Verify the Sources page still loads with no console errors.

---

## Verification (end-to-end)

1. `docker compose build api` succeeds; `docker compose run --rm api which akashic-scanner` prints `/usr/local/bin/akashic-scanner`.
2. `docker compose run --rm api pytest tests/test_source_test_endpoint.py -v` exits 0.
3. `cd scanner && go test ./cmd/akashic-scanner/...` includes the new test_connection_test.go and exits 0.
4. Open http://localhost:3000/sources, click Add source.
5. Pick **Local**, enter a valid path, click Test connection → green "Connection OK".
6. Click Add source → entry appears in the list, no console errors.
7. Pick **SSH**, fill in host/user/key/known_hosts → Test connection → if reachable, green; if not, the failing step is shown.
8. Pick **SMB**, fill in host/share/user/password → Test connection → step:reason shown on failure.
9. Pick **S3** with MinIO endpoint or AWS creds → Test connection → green.
10. Pick **NFS** → Test shows "NFS connection test not yet supported"; save still works.
11. `/admin/audit` shows `source_test_run` events for each test attempt; no passwords or secret_access_key in the payload.
12. Existing source list, scan-trigger, delete still work (no regression).

---

## Out of scope (deferred)

- **NFS test-connection logic** — saved without pre-flight check until a follow-up. The scanner's NFS connector behavior is the reference; reusing it for a test path is a small follow-up but separable.
- **Editing existing sources** through the form. Today only `useUpdateSource` patches connection_config in place; UI for it is separate.
- **Source detail page** — the existing card-grid stays.
- **Bucket/share browser** — for picking S3 buckets or SMB shares interactively. Manual entry only in B1.
