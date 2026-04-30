"""On-demand SID-to-principal cache populated by /api/principals/resolve.

Distinct from `principal_groups_cache` (which records group-membership
of an already-known principal). This table answers "what is the human
name of this SID?" — populated when the web app loads an entry's ACL
and the scanner couldn't resolve the SID at scan time.

Why a per-source cache: the same SID can mean different things across
two SMB servers in different domains (e.g., S-1-5-21-…-1001 in domain
A vs. domain B). Scoping the cache by source prevents cross-domain
poisoning.

Negative caching: when LSARPC genuinely couldn't resolve a SID (e.g.,
DC unreachable, SID belongs to a deleted account), we still write a
row with name=NULL and last_attempt_at set. That keeps us from
hammering the DC every time the user opens the same entry. The TTL
distinction between resolved (`name` non-NULL) and unresolved
(`name` NULL) lives in the resolver service, not the model.
"""
import uuid
from datetime import datetime

from sqlalchemy import String, DateTime, ForeignKey, PrimaryKeyConstraint, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from akashic.database import Base


class PrincipalsCache(Base):
    """One row per (source, sid). Composite primary key."""
    __tablename__ = "principals_cache"

    source_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("sources.id", ondelete="CASCADE"), nullable=False,
    )
    sid: Mapped[str] = mapped_column(String, nullable=False)
    # Display name (e.g., "DOMAIN\jdoe", "BUILTIN\Administrators"). NULL
    # means "tried to resolve, couldn't" — see resolved_at / last_attempt_at
    # for the staleness story.
    name: Mapped[str | None] = mapped_column(String, nullable=True)
    domain: Mapped[str | None] = mapped_column(String, nullable=True)
    # SID type from LSARPC: "user", "group", "alias", "well_known_group",
    # "deleted_account", "invalid", "unknown". NULL for negative cache.
    kind: Mapped[str | None] = mapped_column(String, nullable=True)
    # When a positive resolution last succeeded. NULL for negative cache.
    resolved_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True,
    )
    # When we LAST asked the DC, regardless of outcome. Used to throttle
    # retries on negative-cache rows so we don't DoS the DC.
    last_attempt_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False,
    )

    __table_args__ = (
        PrimaryKeyConstraint("source_id", "sid", name="pk_principals_cache"),
    )
