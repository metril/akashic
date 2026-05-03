# Webhooks

Akashic POSTs a JSON payload to your service when scan lifecycle
events fire. Webhooks are configured **per-user** via the API;
each webhook fires only for events on sources its owner can
read.

## Lifecycle events

A scan transitions through statuses (`pending` → `running` →
`completed` / `failed` / `cancelled`); each transition that
arrives via an ingest batch fires one webhook event:

| Event type | When |
|------------|------|
| `scan.pending` | A scan is queued (rare in practice — scans usually transition straight through pending). |
| `scan.running` | A scan started executing on a scanner agent. |
| `scan.completed` | A scan finished successfully. |
| `scan.failed` | A scan terminated with an error. |
| `scan.cancelled` | A scan was cancelled before it finished. |

A webhook subscribes to **one** event type. Register multiple
webhooks if you need to listen for multiple events.

## Payload shape

```json
{
  "event": "scan.completed",
  "scan_id": "<uuid>",
  "source_id": "<uuid>"
}
```

## Signature verification

The body is signed with the webhook's `secret` using HMAC-SHA256.
The signature is delivered as a hex-encoded string in the
`X-Akashic-Signature` header — **no `sha256=` prefix**, just the
hex digest.

Verify before trusting any payload:

```python
import hmac
import hashlib

def verify(body: bytes, header: str, secret: str) -> bool:
    expected = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(header, expected)
```

The body is JSON-serialised with `sort_keys=True` server-side, so
your verification can rely on a stable byte sequence per event.

## Configuration

There's no Settings UI yet — webhooks are managed via the API
under your own user.

### Create

```http
POST /api/webhooks
Content-Type: application/json
Authorization: Bearer <your JWT>

{
  "event_type": "scan.completed",
  "url": "https://your-service.example.com/akashic-events",
  "secret": "<your shared secret>"
}
```

Returns the created webhook with its `id`. Repeat the call with
different `event_type` values to subscribe to multiple events.

### List your own webhooks

```http
GET /api/webhooks
Authorization: Bearer <your JWT>
```

Returns webhooks you own.

### Delete

```http
DELETE /api/webhooks/{id}
Authorization: Bearer <your JWT>
```

Returns 204. Only the owner can delete.

## Visibility scoping

A webhook fires only for events on sources its owner has
permission to read (admin, or holds an explicit
`SourcePermission` row). For deployment-wide hooks (e.g. ChatOps
"scan finished" notifications across every source), register the
hook under an admin account.

## Reliability

- **Best effort.** A 10-second per-call HTTP timeout. No automatic
  retries on failure.
- Failures are logged server-side at `WARNING` level; they are
  not currently surfaced in the audit log or the UI. Watch your
  receiver's logs.
- Recommend timing out fast (< 5s) on your receiver — the API
  awaits the POST before moving on.
