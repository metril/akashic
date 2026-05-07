"""Outbound URL guard for SSRF defense.

Used by webhook create + dispatch to prevent server-side request
forgery: an authenticated user registering a webhook URL that points
at internal services (postgres, redis, meilisearch), localhost,
RFC1918 ranges, link-local (incl. cloud metadata 169.254.169.254),
or non-http(s) schemes (file://, gopher://, etc.).

Two-stage defense:
- ``validate_outbound_url`` — called from the schema at create-time,
  rejects obvious bad URLs before the row is persisted.
- ``assert_safe_to_dispatch`` — called from the dispatcher, re-resolves
  the hostname so a DNS record that flipped to a private IP after
  webhook creation still gets blocked. (Doesn't fully prevent DNS
  rebinding mid-request — would need a custom transport for that —
  but closes the create→dispatch window.)
"""

from __future__ import annotations

import ipaddress
import socket
from urllib.parse import urlparse

_ALLOWED_SCHEMES = {"http", "https"}


class UnsafeURL(ValueError):
    """Raised when a URL fails the SSRF guard."""


def _is_blocked_ip(addr: str) -> bool:
    """True if ``addr`` (numeric IPv4/IPv6 string) belongs to any
    range we refuse to dispatch to."""
    try:
        ip = ipaddress.ip_address(addr)
    except ValueError:
        return True  # un-parseable → reject
    return (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_multicast
        or ip.is_reserved
        or ip.is_unspecified
    )


def _resolve_all(hostname: str) -> list[str]:
    """Return every numeric address ``hostname`` resolves to.
    Empty list means resolution failed (caller treats as unsafe)."""
    try:
        infos = socket.getaddrinfo(hostname, None)
    except socket.gaierror:
        return []
    return [info[4][0] for info in infos]


def validate_outbound_url(url: str) -> str:
    """Parse + sanity-check ``url``. Returns it unchanged on success;
    raises ``UnsafeURL`` on any of:
    - non-http(s) scheme
    - missing or non-resolvable hostname
    - hostname resolves to a private/loopback/link-local/etc. IP
    - URL is itself a literal IP in a blocked range
    """
    parsed = urlparse(url)
    if parsed.scheme.lower() not in _ALLOWED_SCHEMES:
        raise UnsafeURL(f"scheme {parsed.scheme!r} not allowed (use http or https)")
    host = parsed.hostname
    if not host:
        raise UnsafeURL("URL is missing a hostname")

    # Literal IP in URL — check directly.
    try:
        ipaddress.ip_address(host)
        if _is_blocked_ip(host):
            raise UnsafeURL(f"host {host!r} is in a blocked range")
        return url
    except ValueError:
        pass  # not a literal IP, fall through to DNS resolution

    addrs = _resolve_all(host)
    if not addrs:
        raise UnsafeURL(f"could not resolve {host!r}")
    for a in addrs:
        if _is_blocked_ip(a):
            raise UnsafeURL(f"{host!r} resolves to blocked address {a!r}")
    return url


def assert_safe_to_dispatch(url: str) -> None:
    """Re-validate at dispatch time. Same checks as ``validate_outbound_url``
    but raises rather than returning the URL — caller already has it."""
    validate_outbound_url(url)
