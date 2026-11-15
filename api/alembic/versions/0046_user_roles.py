"""users: role check constraint and role_pinned

Revision ID: 0046_user_roles
Revises: 0045_api_tokens
Create Date: 2026-11-15
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0046_user_roles"
down_revision: Union[str, None] = "0045_api_tokens"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Unknown legacy values already resolve to the viewer bundle at runtime.
    op.execute("UPDATE users SET role = 'viewer' WHERE role IS NULL OR role NOT IN ('viewer', 'admin')")
    op.alter_column("users", "role", existing_type=sa.String(), nullable=False)
    op.add_column(
        "users",
        sa.Column("role_pinned", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.create_check_constraint("ck_users_role", "users", "role IN ('viewer', 'admin')")
    # Existing non-local admins were promoted by hand: group mapping must not demote them.
    op.execute("UPDATE users SET role_pinned = true WHERE role = 'admin' AND auth_provider <> 'local'")


def downgrade() -> None:
    op.drop_constraint("ck_users_role", "users", type_="check")
    op.drop_column("users", "role_pinned")
    op.alter_column("users", "role", existing_type=sa.String(), nullable=True)
