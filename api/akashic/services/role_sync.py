"""Role changes: IdP group mapping and the last-admin guard."""
from __future__ import annotations

import logging

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from akashic.models.user import User
from akashic.services.audit import record_event

logger = logging.getLogger(__name__)

_ROLE_LOCK_KEY = 0x414B5348_02  # arbitrary constant: "AKSH"+2


def _norm(value: str) -> str:
    """Case-insensitive comparison key; DN-shaped values ignore spacing differences."""
    v = value.strip()
    if "=" in v:
        try:
            import ldap.dn

            return ldap.dn.dn2str(ldap.dn.str2dn(v)).lower()
        except Exception:  # noqa: BLE001 - not a parsable DN
            pass
    return v.lower()


async def lock_roles(db: AsyncSession, user: User) -> None:
    """Take the role lock (held until commit/rollback), then reload *user*'s
    role fields so decisions use the committed state, not a stale read."""
    await db.execute(text("SELECT pg_advisory_xact_lock(:k)"), {"k": _ROLE_LOCK_KEY})
    await db.refresh(user, ["role", "role_pinned"])


async def other_admin_exists(db: AsyncSession, user: User) -> bool:
    """Whether an admin other than *user* exists. Call after `lock_roles`."""
    n = (await db.execute(
        select(func.count(User.id)).where(User.role == "admin", User.id != user.id)
    )).scalar()
    return bool(n)


async def apply_group_role(
    db: AsyncSession,
    user: User,
    groups: set[str],
    admin_groups: list[str],
    via: str,
) -> None:
    """Make *user* admin when in one of *admin_groups*, otherwise viewer.

    No-op when no admin groups are configured or the user's role is pinned.
    Never demotes the last admin."""
    wanted = {_norm(g) for g in admin_groups if g and g.strip()}
    if not wanted:
        return
    await lock_roles(db, user)
    if user.role_pinned:
        await db.commit()  # releases the role lock
        return
    target = "admin" if any(_norm(g) in wanted for g in groups) else "viewer"
    if target == user.role:
        await db.commit()
        return
    if target == "viewer" and not await other_admin_exists(db, user):
        name = user.username
        await db.commit()
        logger.warning("role sync (%s): not demoting %s, the last admin", via, name)
        return
    old = user.role
    user.role = target
    await record_event(
        db=db, user=user, event_type="user_role_changed", commit=False,
        payload={
            "target_user_id": str(user.id), "username": user.username,
            "old": old, "new": target, "pinned": False, "via": via,
        },
    )
    await db.commit()
