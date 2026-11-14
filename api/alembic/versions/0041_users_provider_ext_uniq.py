"""users (auth_provider, external_id) unique index

Prevents duplicate SSO/LDAP user rows from concurrent first logins.
No rows are deleted: if duplicates already exist the migration aborts
with the offending pairs so an admin can merge them deliberately.

Revision ID: 0041_users_provider_ext_uniq
Revises: 0040_scanner_concurrency
Create Date: 2026-11-14
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0041_users_provider_ext_uniq"
down_revision: Union[str, None] = "0040_scanner_concurrency"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    dups = bind.execute(sa.text(
        "SELECT auth_provider, external_id, count(*) FROM users "
        "WHERE external_id IS NOT NULL "
        "GROUP BY auth_provider, external_id HAVING count(*) > 1"
    )).fetchall()
    if dups:
        listing = ", ".join(f"({r[0]}, {r[1]}) x{r[2]}" for r in dups[:20])
        raise RuntimeError(
            "Cannot add unique index uq_users_provider_ext: duplicate users share "
            f"(auth_provider, external_id): {listing}. Merge or delete the duplicate "
            "rows, then re-run the migration."
        )
    op.create_index(
        "uq_users_provider_ext", "users", ["auth_provider", "external_id"],
        unique=True, postgresql_where=sa.text("external_id IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index("uq_users_provider_ext", table_name="users")
