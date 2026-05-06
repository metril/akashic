"""OAuth provider registry — endpoints, scopes, account-info hooks.

Static metadata used by ``source_oauth.py`` to drive the
authorization-code + refresh-token flow. Each entry knows:

* ``auth_url``   — where to redirect the user
* ``token_url``  — code/refresh exchange endpoint
* ``scopes``     — list of OAuth scopes to request
* ``userinfo_url`` — endpoint that returns the connected account's
                     email / display name (used to populate
                     ``account_email`` / ``account_label`` on the
                     credential row)
* ``extra_auth_params`` — provider-specific bits that have to be on
                          the auth URL (Google: ``access_type=offline``
                          + ``prompt=consent`` for refresh tokens;
                          Microsoft: ``response_mode=query``)

Box is OAuth + JWT app-auth; the JWT variant doesn't go through this
flow at all and stays out of the registry.
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class OAuthProvider:
    name: str
    label: str
    auth_url: str
    token_url: str
    scopes: list[str]
    userinfo_url: str
    extra_auth_params: dict[str, str] = field(default_factory=dict)
    # The userinfo response field that holds the user's email. None if the
    # provider exposes the email on a separate endpoint we'd need to wire
    # up — currently every supported provider ships email in userinfo.
    email_field: str = "email"
    name_field: str = "name"


_PROVIDERS: dict[str, OAuthProvider] = {
    "google": OAuthProvider(
        name="google",
        label="Google",
        auth_url="https://accounts.google.com/o/oauth2/v2/auth",
        token_url="https://oauth2.googleapis.com/token",
        scopes=[
            "openid",
            "email",
            "profile",
            "https://www.googleapis.com/auth/drive.readonly",
        ],
        userinfo_url="https://openidconnect.googleapis.com/v1/userinfo",
        extra_auth_params={
            # access_type=offline + prompt=consent are what get Google
            # to issue a refresh token. Without prompt=consent, repeat
            # authorizations skip refresh-token issuance.
            "access_type": "offline",
            "prompt": "consent",
            "include_granted_scopes": "true",
        },
    ),
    "microsoft": OAuthProvider(
        name="microsoft",
        label="Microsoft (OneDrive / SharePoint)",
        # /common/ accepts both consumer and work/school accounts. Per-
        # tenant deployments can override by setting redirect_uri to a
        # tenant-specific app registration.
        auth_url="https://login.microsoftonline.com/common/oauth2/v2.0/authorize",
        token_url="https://login.microsoftonline.com/common/oauth2/v2.0/token",
        scopes=[
            "openid",
            "email",
            "profile",
            "offline_access",
            "Files.Read",
            "Files.Read.All",
            "Sites.Read.All",
        ],
        userinfo_url="https://graph.microsoft.com/v1.0/me",
        extra_auth_params={"response_mode": "query"},
        email_field="mail",
        name_field="displayName",
    ),
    "dropbox": OAuthProvider(
        name="dropbox",
        label="Dropbox",
        auth_url="https://www.dropbox.com/oauth2/authorize",
        token_url="https://api.dropboxapi.com/oauth2/token",
        # Dropbox doesn't use space-separated scopes the same way as
        # OAuth-2-canonical providers; the ones we list cover read-only
        # access + account info.
        scopes=[
            "account_info.read",
            "files.metadata.read",
            "files.content.read",
            "sharing.read",
        ],
        userinfo_url="https://api.dropboxapi.com/2/users/get_current_account",
        extra_auth_params={
            # Refresh tokens require token_access_type=offline.
            "token_access_type": "offline",
        },
        email_field="email",
        name_field="name",
    ),
    "box": OAuthProvider(
        name="box",
        label="Box",
        auth_url="https://account.box.com/api/oauth2/authorize",
        token_url="https://api.box.com/oauth2/token",
        # Box scopes are coarse — root_readonly covers read access to
        # files and folders the OAuth user can see.
        scopes=["root_readonly"],
        userinfo_url="https://api.box.com/2.0/users/me",
        email_field="login",
        name_field="name",
    ),
}


def get_provider(name: str) -> OAuthProvider:
    try:
        return _PROVIDERS[name]
    except KeyError as exc:
        raise KeyError(f"Unknown OAuth provider: {name!r}") from exc


def known_providers() -> list[str]:
    return list(_PROVIDERS.keys())


__all__ = ["OAuthProvider", "get_provider", "known_providers"]
