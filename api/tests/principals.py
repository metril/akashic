"""Helpers for tests that stand in for an authenticated caller."""
from __future__ import annotations

import inspect

from akashic.auth.dependencies import Principal


def as_principal(user_or_factory, scopes=None):
    """Dependency override for ``get_principal``.

    Accepts a User, or a (sync/async) callable returning one. ``scopes``
    overrides the role bundle, to test a caller lacking a scope."""

    async def _override() -> Principal:
        u = user_or_factory
        if callable(u):
            u = u()
            if inspect.isawaitable(u):
                u = await u
        p = Principal.for_user(u)
        return Principal(user=u, scopes=frozenset(scopes), via=p.via) if scopes is not None else p

    return _override
