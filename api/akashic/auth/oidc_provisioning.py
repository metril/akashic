"""Bridge from OIDC ID-token claims to FsPerson/FsBinding records.

The OIDC code-flow already creates a User row (api/akashic/auth/oidc.py).
This module turns the claims into the identity layer that powers Search
and Browse permission filtering: one FsPerson per OIDC user, one
FsBinding per matching source. Identities the IdP gave us but no source
matches go into fs_unbound_identities so an admin can see the gap.

See docs/oidc-authentik.md for the deployment guide. Phase 2b adds the
ldap_fallback path here; this file's contract is stable across both
sub-PRs.
"""
from __future__ import annotations

import base64
import binascii
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Literal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from akashic.config import Settings
from akashic.models.fs_person import FsBinding, FsPerson
from akashic.models.fs_unbound_identity import FsUnboundIdentity
from akashic.models.source import Source
from akashic.models.user import User

logger = logging.getLogger(__name__)


IdentityType = Literal["sid", "posix_uid", "nfsv4_principal"]
Confidence = Literal["claim", "ldap", "name"]


@dataclass
class ExtractedIdentity:
    """An identity Akashic believes the logged-in user owns.

    `groups` are stored in the same vocabulary as `identifier` —
    SIDs accompany SIDs, POSIX gids accompany POSIX uids, etc.
    `confidence` records which strategy produced this identity, so
    the SettingsIdentities UI can badge `claim` (best, IdP-issued)
    vs `ldap` (Akashic looked it up itself) vs `name` (string match,
    weakest)."""

    identity_type: IdentityType
    identifier: str
    groups: list[str]
    confidence: Confidence


# ── objectSid decoding ──────────────────────────────────────────────────────


def _decode_object_sid(value: Any) -> str | None:
    """Normalize an `objectSid`-shaped claim into the canonical
    `S-1-5-21-…` string. Authentik's AD federation can emit either:
      - The string form already (when the property mapper converts on the
        Authentik side, recommended).
      - A base64-encoded 28-byte binary blob (the LDAP wire format).

    Returns None if the input doesn't look like either."""
    if value is None:
        return None
    if isinstance(value, list):
        # `groups` may pack a single objectSid into a list of one. Take
        # the first non-empty entry.
        value = next((v for v in value if v), None)
        if value is None:
            return None
    if not isinstance(value, str):
        return None
    s = value.strip()
    if s.startswith("S-1-") or s.startswith("s-1-"):
        return s.upper()
    # Try base64 binary-SID. SIDs are 8 + 4*sub_authority_count bytes.
    try:
        raw = base64.b64decode(s, validate=False)
    except (binascii.Error, ValueError):
        return None
    if len(raw) < 8:
        return None
    revision = raw[0]
    sub_auth_count = raw[1]
    expected_len = 8 + 4 * sub_auth_count
    if len(raw) != expected_len or sub_auth_count > 15:
        return None
    # Identifier authority is a 6-byte big-endian integer.
    ident_auth = int.from_bytes(raw[2:8], "big")
    parts = [str(revision), str(ident_auth)]
    for i in range(sub_auth_count):
        offset = 8 + 4 * i
        sa = int.from_bytes(raw[offset:offset + 4], "little")
        parts.append(str(sa))
    return "S-" + "-".join(parts)


# ── Strategy: claim ─────────────────────────────────────────────────────────


def _from_claim_strategy(
    claims: dict, settings: Settings,
) -> list[ExtractedIdentity]:
    """Pure claim-extraction. Used by `claim` strategy and as the first
    leg of `auto`. Returns [] if the IdP didn't emit usable identity claims."""
    out: list[ExtractedIdentity] = []

    # User SID. Either as a top-level claim or nested under
    # `attributes.<claim>` (Authentik's user-attributes shape).
    sid_raw = claims.get(settings.oidc_sid_claim)
    if sid_raw is None:
        attrs = claims.get("attributes")
        if isinstance(attrs, dict):
            sid_raw = attrs.get(settings.oidc_sid_claim)
    sid = _decode_object_sid(sid_raw)

    # Group identifiers. We carry them as either SIDs (best) or names —
    # the search side handles both.
    groups_raw = claims.get(settings.oidc_groups_claim) or []
    if not isinstance(groups_raw, list):
        groups_raw = [groups_raw] if groups_raw else []

    if settings.oidc_groups_format == "sid":
        groups = [g for g in (_decode_object_sid(v) for v in groups_raw) if g]
    elif settings.oidc_groups_format == "name":
        groups = [str(v) for v in groups_raw if v]
    else:  # dn
        # DN form is parsed by group_resolver in Phase 2b; here we just
        # carry the raw strings forward.
        groups = [str(v) for v in groups_raw if v]

    if sid:
        out.append(ExtractedIdentity(
            identity_type="sid",
            identifier=sid,
            groups=groups,
            confidence="claim",
        ))

    # POSIX uid claim — useful for SSH/NFS sources where users are
    # identified by uid rather than SID. groups in this branch are POSIX
    # group names (the IdP rarely emits gid numbers; matching on names
    # against owner_name/group_name in the indexed entries is the
    # realistic path).
    uid_raw = claims.get(settings.oidc_uid_claim)
    if uid_raw not in (None, ""):
        try:
            uid = str(int(uid_raw))
            out.append(ExtractedIdentity(
                identity_type="posix_uid",
                identifier=uid,
                groups=[str(v) for v in groups_raw if v],
                confidence="claim",
            ))
        except (TypeError, ValueError):
            pass

    return out


# ── Strategy: name_match ────────────────────────────────────────────────────


def _from_name_match_strategy(
    claims: dict, settings: Settings,
) -> list[ExtractedIdentity]:
    """Last-resort fallback for IdPs that emit no SIDs and no POSIX uids.

    We synthesise a posix_uid binding with identifier='-1' (the same
    sentinel acl_denorm uses for "any member of this group") and pass
    the name-form group claims through. The search-time path already
    matches `posix:gid:<groupname>` tokens against the name strings
    that scanner-extracted ACLs put in entry.group_name, so this
    bridges name-only OIDC into permission-aware search for POSIX/NFS
    sources at the cost of less precision against SMB shares."""
    groups_raw = claims.get(settings.oidc_groups_claim) or []
    if not isinstance(groups_raw, list):
        groups_raw = [groups_raw] if groups_raw else []
    groups = [str(v) for v in groups_raw if v]

    if not groups:
        return []
    return [ExtractedIdentity(
        identity_type="posix_uid",
        identifier="-1",
        groups=groups,
        confidence="name",
    )]


# ── Public extraction ──────────────────────────────────────────────────────


def extract_identities(
    claims: dict, settings: Settings,
) -> list[ExtractedIdentity]:
    """Apply the configured strategy and return the candidate set.

    Phase 2a covers `claim` and `name_match` paths. The `ldap_fallback`
    path is a Phase 2b extension — `auto` falls through to `name_match`
    until that lands."""
    strategy = settings.oidc_strategy

    if strategy == "claim":
        return _from_claim_strategy(claims, settings)
    if strategy == "name_match":
        return _from_name_match_strategy(claims, settings)
    if strategy == "ldap_fallback":
        # Phase 2b. Until that ships, fall back to claim+name so
        # deployments setting this strategy keep working at name-match
        # precision rather than 401-ing.
        return _from_claim_strategy(claims, settings) or _from_name_match_strategy(claims, settings)

    # auto: claim → ldap_fallback → name_match. Try claim first; if
    # it returns anything, use it (claims trump fallbacks). Otherwise
    # fall through to name_match.
    claim_results = _from_claim_strategy(claims, settings)
    if claim_results:
        return claim_results
    return _from_name_match_strategy(claims, settings)


# ── Source-domain matching ────────────────────────────────────────────────


def _source_matches(source: Source, identity: ExtractedIdentity) -> bool:
    """Decide whether this identity should bind to this source.

    For `sid` identities, we check the source's
    `connection_config.principal_domain` — the SID prefix that
    identifies the AD domain, e.g. `S-1-5-21-1234567890-987654321`.
    Identities whose SID starts with that prefix bind; others don't.

    For `posix_uid` identities we bind to local/ssh/nfs sources by
    default — the `identifier='-1'` group-only synthetic binding from
    name_match has nothing else to discriminate on, and over-binding
    is acceptable because the actual permission filter still requires
    the group-name token to match an indexed ACL.

    `s3_canonical` and `nfsv4_principal` aren't currently emitted by
    extract_identities; bind only to sources of the matching type.
    """
    cfg = source.connection_config or {}
    src_type = source.type or ""

    if identity.identity_type == "sid":
        domain = (cfg.get("principal_domain") or "").strip().upper()
        if not domain:
            # No principal_domain configured. Bind only to SMB sources
            # to limit accidental cross-binding; admins should still
            # configure principal_domain explicitly.
            return src_type == "smb"
        return identity.identifier.upper().startswith(domain.upper())

    if identity.identity_type == "posix_uid":
        return src_type in ("local", "ssh", "nfs")

    if identity.identity_type == "nfsv4_principal":
        return src_type == "nfs"

    return False


# ── FsPerson / FsBinding sync ──────────────────────────────────────────────


_OIDC_PERSON_LABEL_PREFIX = "OIDC: "


async def _ensure_person(db: AsyncSession, user: User) -> FsPerson:
    """Find the user's OIDC FsPerson, creating one if absent.

    A user has at most one OIDC-authored FsPerson. The label format
    `OIDC: <username>` lets a human admin distinguish it from manually-
    created FsPersons in Settings → Identities."""
    label = f"{_OIDC_PERSON_LABEL_PREFIX}{user.username}"
    row = (
        await db.execute(
            select(FsPerson).where(
                FsPerson.user_id == user.id,
                FsPerson.label == label,
            )
        )
    ).scalar_one_or_none()
    if row is not None:
        return row
    person = FsPerson(user_id=user.id, label=label, is_primary=False)
    db.add(person)
    await db.flush()
    await db.refresh(person)
    return person


async def _upsert_binding(
    db: AsyncSession,
    person: FsPerson,
    source_id,
    identity: ExtractedIdentity,
) -> None:
    """Upsert one FsBinding for (person, source). Replaces identifier+groups
    if the existing row's identifier still matches; otherwise rewrites."""
    existing = (
        await db.execute(
            select(FsBinding).where(
                FsBinding.fs_person_id == person.id,
                FsBinding.source_id == source_id,
            )
        )
    ).scalar_one_or_none()
    now = datetime.now(timezone.utc)
    if existing is None:
        db.add(FsBinding(
            fs_person_id=person.id,
            source_id=source_id,
            identity_type=identity.identity_type,
            identifier=identity.identifier,
            groups=identity.groups,
            groups_source="auto",
            groups_resolved_at=now,
        ))
        return
    # Re-author the existing row from the latest claims. We trust the
    # IdP over a manually-created binding for OIDC-driven persons.
    existing.identity_type = identity.identity_type
    existing.identifier = identity.identifier
    existing.groups = identity.groups
    existing.groups_source = "auto"
    existing.groups_resolved_at = now


async def _record_unbound(
    db: AsyncSession, user: User, identity: ExtractedIdentity,
) -> None:
    """Add or refresh the user's row in fs_unbound_identities."""
    existing = (
        await db.execute(
            select(FsUnboundIdentity).where(
                FsUnboundIdentity.user_id == user.id,
                FsUnboundIdentity.identity_type == identity.identity_type,
                FsUnboundIdentity.identifier == identity.identifier,
            )
        )
    ).scalar_one_or_none()
    now = datetime.now(timezone.utc)
    if existing is None:
        db.add(FsUnboundIdentity(
            user_id=user.id,
            identity_type=identity.identity_type,
            identifier=identity.identifier,
            confidence=identity.confidence,
            groups=identity.groups,
            first_seen_at=now,
            last_seen_at=now,
        ))
    else:
        existing.confidence = identity.confidence
        existing.groups = identity.groups
        existing.last_seen_at = now


async def sync_fs_bindings_from_claims(
    db: AsyncSession,
    user: User,
    claims: dict,
    settings: Settings,
) -> None:
    """Run extract_identities and project the result onto FsBinding +
    FsUnboundIdentity rows.

    Caller is responsible for the surrounding commit. Failures are
    logged and swallowed — a bad claims payload must not block the
    user's login."""
    try:
        identities = extract_identities(claims, settings)
    except Exception:  # noqa: BLE001
        logger.exception("OIDC identity extraction failed for user %s", user.id)
        return

    if not identities:
        return

    person = await _ensure_person(db, user)

    sources = (await db.execute(select(Source))).scalars().all()

    for identity in identities:
        matched = [s for s in sources if _source_matches(s, identity)]
        if matched:
            for source in matched:
                await _upsert_binding(db, person, source.id, identity)
        else:
            await _record_unbound(db, user, identity)
