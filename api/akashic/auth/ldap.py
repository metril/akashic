"""LDAP bind authentication for enterprise environments.

Flow:
  1. Bind to the LDAP server with the service-account credentials.
  2. Search for the user's DN using the configured filter.
  3. Attempt a second bind with the user's DN and the supplied password.
  4. Return a dict of user attributes on success, or None on failure.
"""

from __future__ import annotations

import logging

import ldap  # python-ldap
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from akashic.config import settings
from akashic.models.user import User

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Core LDAP authentication
# ---------------------------------------------------------------------------


def authenticate_ldap(username: str, password: str) -> dict | None:
    """Authenticate *username* / *password* against the configured LDAP server.

    Returns a dict of user attributes (``dn``, ``uid``, ``mail``, ``cn``) on
    success, or ``None`` if authentication fails for any reason.

    Note: python-ldap is synchronous; this function is intentionally *not*
    async.  Call it from a thread pool (``asyncio.get_event_loop().run_in_executor``)
    if you need non-blocking behaviour inside an async route.
    """
    try:
        conn = ldap.initialize(settings.ldap_server)
        conn.set_option(ldap.OPT_REFERRALS, 0)
        conn.set_option(ldap.OPT_NETWORK_TIMEOUT, 5)

        # Step 1 — service-account bind to search for the user
        conn.simple_bind_s(settings.ldap_bind_dn, settings.ldap_bind_password)

        # Step 2 — find the user's DN
        search_filter = settings.ldap_user_filter.format(username=ldap.filter.escape_filter_chars(username))
        results = conn.search_s(
            settings.ldap_user_base,
            ldap.SCOPE_SUBTREE,
            search_filter,
            ["dn", "uid", "mail", "cn", "sAMAccountName", "userPrincipalName"],
        )

        if not results:
            logger.debug("LDAP: no entry found for username=%s", username)
            return None

        user_dn, attrs = results[0]
        if not user_dn:
            return None

        # Step 3 — bind as the user to verify the password
        user_conn = ldap.initialize(settings.ldap_server)
        user_conn.set_option(ldap.OPT_REFERRALS, 0)
        user_conn.set_option(ldap.OPT_NETWORK_TIMEOUT, 5)
        user_conn.simple_bind_s(user_dn, password)

        # Step 4 — extract useful attributes
        def _first(key: str) -> str | None:
            values = attrs.get(key)
            if not values:
                return None
            v = values[0]
            return v.decode() if isinstance(v, bytes) else v

        uid = (
            _first("uid")
            or _first("sAMAccountName")
            or _first("userPrincipalName")
            or username
        )
        return {
            "dn": user_dn,
            "uid": uid,
            "email": _first("mail"),
            "display_name": _first("cn"),
        }

    except ldap.INVALID_CREDENTIALS:
        logger.debug("LDAP: invalid credentials for username=%s", username)
        return None
    except ldap.LDAPError as exc:
        logger.warning("LDAP error during authentication for %s: %s", username, exc)
        return None


# ---------------------------------------------------------------------------
# User provisioning
# ---------------------------------------------------------------------------


async def get_or_create_user(db: AsyncSession, ldap_info: dict) -> User:
    """Find an existing user by external_id, or auto-provision a new one.

    The user's LDAP DN is stored as ``external_id``.
    New users are given the ``viewer`` role.
    """
    dn = ldap_info["dn"]

    result = await db.execute(
        select(User).where(
            User.auth_provider == "ldap",
            User.external_id == dn,
        )
    )
    user = result.scalar_one_or_none()

    if user is None:
        base_username = ldap_info.get("uid") or dn
        username = base_username
        counter = 1
        while True:
            existing = await db.execute(select(User).where(User.username == username))
            if existing.scalar_one_or_none() is None:
                break
            username = f"{base_username}_{counter}"
            counter += 1

        user = User(
            username=username,
            email=ldap_info.get("email"),
            role="viewer",
            auth_provider="ldap",
            external_id=dn,
        )
        db.add(user)
        await db.commit()
        await db.refresh(user)

    return user
