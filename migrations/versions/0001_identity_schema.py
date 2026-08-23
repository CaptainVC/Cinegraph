"""Create identity accounts, sessions, and normalized entitlements.

Revision ID: 0001_identity_schema
Revises:
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0001_identity_schema"
down_revision: str | None = None
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    uuid_type = sa.Uuid(as_uuid=True)
    op.create_table(
        "user_accounts",
        sa.Column("user_id", uuid_type, primary_key=True),
        sa.Column("profile_id", uuid_type, nullable=False),
        sa.Column("email", sa.String(length=320), nullable=False),
        sa.Column("display_name", sa.String(length=200), nullable=False),
        sa.Column("password_hash", sa.String(length=512), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("profile_id", name="uq_user_accounts_profile_id"),
        sa.UniqueConstraint("email", name="uq_user_accounts_email"),
        sa.UniqueConstraint("user_id", "profile_id", name="uq_user_accounts_user_profile"),
        sa.CheckConstraint(
            "status IN ('active', 'disabled')",
            name="ck_user_accounts_status",
        ),
    )
    op.create_table(
        "sessions",
        sa.Column("session_id", uuid_type, primary_key=True),
        sa.Column("token_sha256", sa.String(length=64), nullable=False),
        sa.Column("principal_kind", sa.String(length=32), nullable=False),
        sa.Column("profile_id", uuid_type, nullable=False),
        sa.Column("user_id", uuid_type, nullable=True),
        sa.Column("access_mode", sa.String(length=32), nullable=False),
        sa.Column("access_revision", sa.String(length=128), nullable=False),
        sa.Column("unrestricted", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["user_id", "profile_id"],
            ["user_accounts.user_id", "user_accounts.profile_id"],
            name="fk_sessions_user_profile",
        ),
        sa.UniqueConstraint("token_sha256", name="uq_sessions_token_sha256"),
        sa.CheckConstraint(
            "((principal_kind = 'guest' AND user_id IS NULL AND access_mode = 'guest' AND unrestricted IS FALSE) "
            "OR (principal_kind = 'authenticated' AND user_id IS NOT NULL AND access_mode = 'authenticated' AND unrestricted IS TRUE))",
            name="ck_sessions_principal_coherence",
        ),
        sa.CheckConstraint("expires_at > created_at", name="ck_sessions_expiry_after_creation"),
        sa.CheckConstraint(
            "revoked_at IS NULL OR (revoked_at >= created_at AND revoked_at <= expires_at)",
            name="ck_sessions_revocation_lifecycle",
        ),
    )
    op.create_index("ix_sessions_expires_at", "sessions", ["expires_at"])
    op.create_index("ix_sessions_profile_id", "sessions", ["profile_id"])
    op.create_table(
        "session_entitlements",
        sa.Column("session_id", uuid_type, nullable=False),
        sa.Column("series_id", uuid_type, nullable=False),
        sa.Column("season_number", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(["session_id"], ["sessions.session_id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("session_id", "series_id", "season_number"),
        sa.CheckConstraint("season_number >= 1", name="ck_session_entitlements_positive_season"),
    )
    op.create_index(
        "ix_session_entitlements_series_season",
        "session_entitlements",
        ["series_id", "season_number"],
    )


def downgrade() -> None:
    op.drop_index("ix_session_entitlements_series_season", table_name="session_entitlements")
    op.drop_table("session_entitlements")
    op.drop_index("ix_sessions_profile_id", table_name="sessions")
    op.drop_index("ix_sessions_expires_at", table_name="sessions")
    op.drop_table("sessions")
    op.drop_table("user_accounts")
