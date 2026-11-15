"""Helpers for tests that stand in for an authenticated caller."""
from __future__ import annotations

import inspect

from akashic.auth.dependencies import Principal


def as_principal(user_or_factory, scopes=None, allowed_source_ids=None):
    """Dependency override for ``get_principal``.

    Accepts a User, or a (sync/async) callable returning one. ``scopes``
    overrides the role bundle, to test a caller lacking a scope.
    ``allowed_source_ids`` simulates a source-limited token."""

    async def _override() -> Principal:
        u = user_or_factory
        if callable(u):
            u = u()
            if inspect.isawaitable(u):
                u = await u
        p = Principal.for_user(u)
        if scopes is None and allowed_source_ids is None:
            return p
        return Principal(
            user=u,
            scopes=frozenset(scopes) if scopes is not None else p.scopes,
            allowed_source_ids=frozenset(allowed_source_ids) if allowed_source_ids is not None else None,
            via="token" if allowed_source_ids is not None else p.via,
        )

    return _override
