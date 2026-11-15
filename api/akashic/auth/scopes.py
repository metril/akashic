"""The scope catalog: the single definition of every permission in the API.

A request is authorised by *scopes*, never by role. A role is just a named
bundle of scopes (``ROLE_SCOPES``); a principal's effective scopes are what
the role bundle grants. Anything not listed here is denied.
"""
from __future__ import annotations

SCOPES: dict[str, str] = {
    "files:read": "Search, browse and read file and directory metadata, including duplicates.",
    "files:content": "Download or preview file contents (kept separate from reading metadata).",
    "tags:read": "List tags and the tags applied to files.",
    "tags:write": "Create and delete tags, and apply or remove them on files.",
    "analytics:read": "Read analytics, the dashboard and the storage explorer.",
    "sources:read": "List sources and read their details, audit trail and reachability.",
    "sources:write": "Create, test, update, delete and re-attach sources.",
    "credentials:read": "List credential profiles and OAuth credentials and providers.",
    "credentials:write": "Create, change and delete credential profiles and OAuth credentials and providers.",
    "scans:read": "Read scans, scan progress and logs, and subscribe to scan streams.",
    "scans:write": "Trigger and cancel scans.",
    "scanners:read": "Read scanners, hosts and their reachability.",
    "scanners:write": "Register, change and remove scanners, hosts and claim tokens; approve discoveries.",
    "identities:read": "Read identities, bindings, principals, effective permissions and access reports.",
    "identities:write": "Manage identities and bindings, and resolve their groups.",
    "webhooks:read": "List webhooks.",
    "webhooks:write": "Create and delete webhooks.",
    "users:read": "Read other users' accounts and unbound identities.",
    "users:write": "Create users, and see files regardless of per-user ACL filtering.",
    "settings:read": "Read server settings.",
    "settings:write": "Change server settings.",
    "audit:read": "Read the admin audit log.",
    "health:read": "Read dependency health and service activity.",
    "maintenance:write": "Run maintenance jobs.",
    "data:purge": "Permanently purge indexed data. Destructive, so it must be granted explicitly.",
    "tokens:read": "List your own API tokens.",
    "tokens:write": "Create, change and revoke your own API tokens.",
    "tokens:admin": "List and revoke every user's API tokens.",
}

# Capability checks that used to be ``role == "admin"``. They are expressed as
# a scope held by the principal, so a narrower token cannot gain them.
ACL_BYPASS_SCOPE = "users:write"  # sees all files regardless of per-user ACL filtering

_VIEWER_READ_EXCLUDED = {"credentials:read", "users:read", "settings:read", "audit:read"}

ROLE_SCOPES: dict[str, frozenset[str]] = {
    "viewer": frozenset(
        {s for s in SCOPES if s.endswith(":read") and s not in _VIEWER_READ_EXCLUDED}
        | {
            "files:content", "tags:write", "tokens:write",
            # Viewers manage their own identities and webhooks.
            "identities:write", "webhooks:write",
        }
    ),
    "admin": frozenset(SCOPES),
}


def scopes_for_role(role: str | None) -> frozenset[str]:
    """Scopes granted by a role; an unknown role grants nothing."""
    return ROLE_SCOPES.get(role or "", frozenset())
