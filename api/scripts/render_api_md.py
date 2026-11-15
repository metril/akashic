"""Render the API reference (``docs/api.md``) from ``openapi.json``.

Stdlib only and deterministic: the same spec always yields the same bytes, so
CI can diff the committed file against a fresh render.

    python api/scripts/render_api_md.py [spec-path [output-path]]
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
METHODS = ("get", "put", "post", "delete", "options", "head", "patch", "trace")


def esc(text: Any) -> str:
    """One-line, pipe-safe table cell."""
    return " ".join(str(text or "").split()).replace("|", "\\|")


def ref_name(ref: str) -> str:
    return ref.rsplit("/", 1)[-1]


def link(name: str) -> str:
    return f"[`{name}`](#schema-{name.lower().replace('_', '-')})"


def type_str(s: dict[str, Any] | None) -> str:
    """Compact, linked rendering of a JSON schema."""
    if not s:
        return "any"
    if "$ref" in s:
        return link(ref_name(s["$ref"]))
    if "const" in s:
        return f"`{json.dumps(s['const'])}`"
    if "enum" in s:
        return " \\| ".join(f"`{json.dumps(v)}`" for v in s["enum"])
    for key, sep in (("anyOf", " \\| "), ("oneOf", " \\| "), ("allOf", " & ")):
        if key in s:
            return sep.join(type_str(x) for x in s[key])
    t = s.get("type")
    if isinstance(t, list):
        return " \\| ".join(f"`{x}`" for x in t)
    if t == "array":
        return f"array of {type_str(s.get('items'))}"
    if t == "object":
        ap = s.get("additionalProperties")
        if isinstance(ap, dict) and not s.get("properties"):
            return f"map of {type_str(ap)}"
        return "`object`"
    if t:
        fmt = s.get("format")
        return f"`{t}`" + (f" (`{fmt}`)" if fmt else "")
    return "any"


def anchor(kind: str, name: str) -> str:
    return f'<a id="{kind}-{name.lower().replace("_", "-")}"></a>'


def render_security(op: dict[str, Any]) -> str:
    sec = op.get("security")
    if not sec:
        return "None (public)"
    parts = []
    for req in sec:
        for name, scopes in sorted(req.items()):
            parts.append(f"`{name}`" + (f", scope " + ", ".join(f"`{x}`" for x in scopes) if scopes else ""))
    return "; ".join(parts)


def render_params(params: list[dict[str, Any]]) -> list[str]:
    if not params:
        return []
    out = ["", "| Parameter | In | Type | Required | Description |", "| --- | --- | --- | --- | --- |"]
    for p in params:
        out.append(
            f"| `{p['name']}` | {p['in']} | {type_str(p.get('schema'))} | "
            f"{'yes' if p.get('required') else 'no'} | {esc(p.get('description') or (p.get('schema') or {}).get('description'))} |"
        )
    return out


def render_body(body: dict[str, Any] | None) -> list[str]:
    if not body:
        return []
    out = ["", f"**Request body**{' (required)' if body.get('required') else ''}"]
    for ctype, media in sorted(body.get("content", {}).items()):
        out.append(f"- `{ctype}`: {type_str(media.get('schema'))}")
    return out


def render_responses(responses: dict[str, Any]) -> list[str]:
    out = ["", "| Status | Description | Body |", "| --- | --- | --- |"]
    for status, r in sorted(responses.items()):
        content = r.get("content") or {}
        bodies = ", ".join(f"`{c}`: {type_str(m.get('schema'))}" for c, m in sorted(content.items())) or "none"
        out.append(f"| {status} | {esc(r.get('description'))} | {bodies} |")
    return out


def render_operation(path: str, method: str, op: dict[str, Any], path_params: list[dict[str, Any]]) -> list[str]:
    out = ["", f"{anchor('op', op['operationId'])}", f"#### `{method.upper()} {path}`", "", f"**{op.get('summary', '')}**" + f" (`{op['operationId']}`)"]
    if op.get("deprecated"):
        out += ["", "_Deprecated._"]
    out += ["", f"Auth: {render_security(op)}"]
    if op.get("description"):
        out += ["", op["description"].strip()]
    out += render_params(path_params + op.get("parameters", []))
    out += render_body(op.get("requestBody"))
    out += render_responses(op.get("responses", {}))
    return out


def render_schema(name: str, s: dict[str, Any]) -> list[str]:
    out = ["", anchor("schema", name), f"### {name}"]
    if s.get("description"):
        out += ["", s["description"].strip()]
    props = s.get("properties")
    if props:
        required = set(s.get("required", []))
        out += ["", "| Field | Type | Required | Description |", "| --- | --- | --- | --- |"]
        for fname, fs in props.items():
            out.append(f"| `{fname}` | {type_str(fs)} | {'yes' if fname in required else 'no'} | {esc(fs.get('description'))} |")
    else:
        out += ["", f"Type: {type_str(s)}"]
        if s.get("discriminator"):
            out.append(f"Discriminator: `{s['discriminator'].get('propertyName')}`")
    return out


def render(spec: dict[str, Any]) -> str:
    info = spec["info"]
    schemes = spec.get("components", {}).get("securitySchemes", {})
    schemas = spec.get("components", {}).get("schemas", {})
    out = [
        "<!-- Generated from openapi.json by api/scripts/render_api_md.py. Do not edit; run `make openapi`. -->",
        f"# {info['title']} API reference",
        "",
        f"Version {info['version']}. Generated from [`openapi.json`](../openapi.json); the live, interactive version is served at `/api/docs`.",
    ]
    if info.get("description"):
        out += ["", info["description"].strip()]
    out += [
        "",
        "## Conventions",
        "",
        "- HTTP endpoints live under `/api/v1`, WebSockets under `/ws/v1`. Unversioned `/api/*` answers 410 `api_version_required`.",
        "- Send `Authorization: Bearer <token>`: a login access token or a personal API token (`akp_...`).",
        "- Lists return `{items, next_cursor, limit, total?}`; pass `next_cursor` back as `cursor`. `null` means the last page.",
        "- Every non-2xx response is an `ErrorResponse` (below) and carries an `X-Request-ID` header.",
        "",
        "## Authentication",
        "",
        "| Scheme | Type | Description |",
        "| --- | --- | --- |",
    ]
    for name, sch in sorted(schemes.items()):
        kind = sch.get("type", "") + (f"/{sch['scheme']}" if sch.get("scheme") else "")
        out.append(f"| `{name}` | {kind} | {esc(sch.get('description'))} |")
    scopes: dict[str, str] = {}
    for sch in schemes.values():
        scopes.update(sch.get("x-scopes", {}))
    if scopes:
        out += ["", "### Scopes", "", "| Scope | Grants |", "| --- | --- |"]
        out += [f"| `{k}` | {esc(v)} |" for k, v in sorted(scopes.items())]

    out += ["", "## Errors", "", "Body of every non-2xx response:"]
    if "ErrorResponse" in schemas:
        out += render_schema("ErrorResponse", schemas["ErrorResponse"])[2:]
    codes = spec.get("x-error-codes", [])
    if codes:
        out += ["", "### Error codes", "", "| Status | `code` | Meaning |", "| --- | --- | --- |"]
        out += [f"| {c['status']} | `{c['code']}` | {esc(c['description'])} |" for c in sorted(codes, key=lambda c: (c["status"], c["code"]))]

    by_tag: dict[str, list[tuple[str, str, dict[str, Any], list[dict[str, Any]]]]] = {}
    for path, item in sorted(spec["paths"].items()):
        for method in METHODS:
            op = item.get(method)
            if op is None:
                continue
            for tag in op.get("tags") or ["untagged"]:
                by_tag.setdefault(tag, []).append((path, method, op, item.get("parameters", [])))
    out += ["", "## Endpoints"]
    declared = [t["name"] for t in spec.get("tags", [])]
    order = declared + sorted(t for t in by_tag if t not in declared)
    for tag in order:
        if tag not in by_tag:
            continue
        desc = next((t.get("description") for t in spec.get("tags", []) if t["name"] == tag), "")
        out += ["", f"### {tag}"]
        if desc:
            out += ["", desc]
        out += [""] + [f"- `{m.upper()} {p}`: {op.get('summary', '')}" for p, m, op, _ in by_tag[tag]]
        for p, m, op, pp in by_tag[tag]:
            out += render_operation(p, m, op, pp)

    sockets = spec.get("x-websockets", [])
    if sockets:
        out += [
            "", "## WebSockets", "",
            "Browsers cannot set headers on the handshake, so authenticate with `?token=<jwt>`, a login access token. Personal API tokens (`akp_...`) are not accepted on WebSockets: the socket is closed with code 1008. "
            "Auth failures close the socket with code 1008 before any frame is sent. Every frame is a JSON object with a `kind` "
            "discriminator; the server sends a `ping` frame every 30 seconds and an `error` frame when its live feed breaks "
            "(fall back to polling and reconnect). On every connect the first frame is a `snapshot`.",
        ]
        for ws in sockets:
            out += ["", f"### `{ws['path']}`", "", f"{ws['description']}", "", "| Event | `kind` | Description |", "| --- | --- | --- |"]
            for ev in ws["events"]:
                es = schemas.get(ev, {})
                kind = es.get("properties", {}).get("kind", {})
                kinds = " / ".join(f"`{k}`" for k in (kind.get("enum") or ([kind["const"]] if "const" in kind else [])))
                out.append(f"| {link(ev)} | {kinds} | {esc(es.get('description'))} |")

    out += ["", "## Schemas"]
    for name in sorted(schemas):
        out += render_schema(name, schemas[name])
    return "\n".join(out) + "\n"


def main() -> None:
    spec_path = Path(sys.argv[1]) if len(sys.argv) > 1 else REPO_ROOT / "openapi.json"
    out_path = Path(sys.argv[2]) if len(sys.argv) > 2 else REPO_ROOT / "docs" / "api.md"
    out_path.write_text(render(json.loads(spec_path.read_text(encoding="utf-8"))), encoding="utf-8")
    print(f"wrote {out_path}")


if __name__ == "__main__":
    main()
