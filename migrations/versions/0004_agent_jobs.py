"""Persist asynchronous agent jobs and privacy-safe replay events."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0004_agent_jobs"
down_revision: str | None = "0003_graph_claims"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    uuid_type = sa.Uuid(as_uuid=True)
    op.create_table(
        "agent_jobs",
        sa.Column("job_id", uuid_type, primary_key=True),
        # Deliberately no FK: guest profiles are not identity rows.
        sa.Column("owner_profile_id", uuid_type, nullable=False),
        sa.Column("thread_id", uuid_type, nullable=False),
        sa.Column("series_id", uuid_type, nullable=False),
        sa.Column("question_json", sa.JSON(), nullable=False),
        sa.Column("candidate_episodes_json", sa.JSON(), nullable=False),
        sa.Column("corpus_access_scope_json", sa.JSON(), nullable=False),
        sa.Column("permission_scope_revision", sa.String(length=128), nullable=False),
        sa.Column("idempotency_key", sa.String(length=36), nullable=False),
        sa.Column("request_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("request_id", sa.String(length=64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("result_json", sa.JSON(), nullable=True),
        sa.Column("error_code", sa.String(length=80), nullable=True),
        sa.UniqueConstraint("owner_profile_id", "idempotency_key", name="uq_agent_jobs_owner_key"),
        sa.CheckConstraint(
            "status IN ('queued','running','succeeded','safe_refusal','failed')",
            name="ck_agent_jobs_status_allowed",
        ),
        sa.CheckConstraint(
            "length(idempotency_key) = 36 AND length(request_fingerprint) = 64",
            name="ck_agent_jobs_identity_nonempty",
        ),
        sa.CheckConstraint(
            "(status = 'queued' AND started_at IS NULL AND finished_at IS NULL AND result_json IS NULL AND error_code IS NULL) OR "
            "(status = 'running' AND started_at IS NOT NULL AND finished_at IS NULL AND result_json IS NULL AND error_code IS NULL) OR "
            "(status IN ('succeeded','safe_refusal') AND started_at IS NOT NULL AND finished_at IS NOT NULL AND result_json IS NOT NULL AND error_code IS NULL) OR "
            "(status = 'failed' AND finished_at IS NOT NULL AND result_json IS NULL AND error_code IS NOT NULL)",
            name="ck_agent_jobs_state_coherent",
        ),
        sa.CheckConstraint(
            "started_at IS NULL OR started_at >= created_at",
            name="ck_agent_jobs_started_after_created",
        ),
        sa.CheckConstraint(
            "finished_at IS NULL OR finished_at >= created_at",
            name="ck_agent_jobs_finished_after_created",
        ),
        sa.CheckConstraint(
            "finished_at IS NULL OR started_at IS NULL OR finished_at >= started_at",
            name="ck_agent_jobs_finished_after_started",
        ),
        sa.CheckConstraint(
            # Frozen copy of the Phase 34 runtime failure taxonomy.
            "error_code IS NULL OR error_code IN "
            "('execution_timeout','provider_unavailable','budget_exceeded',"
            "'agent_execution_failed','agent_dispatch_unavailable')",
            name="ck_agent_jobs_error_code_allowed",
        ),
    )
    op.create_index(
        "ix_agent_jobs_owner_status_created",
        "agent_jobs",
        ["owner_profile_id", "status", "created_at"],
    )
    op.create_index("ix_agent_jobs_status_created", "agent_jobs", ["status", "created_at"])
    op.create_table(
        "agent_job_events",
        sa.Column("event_id", uuid_type, primary_key=True),
        sa.Column("job_id", uuid_type, nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("kind", sa.String(length=24), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("payload_json", sa.JSON(), nullable=False),
        sa.ForeignKeyConstraint(
            ["job_id"], ["agent_jobs.job_id"], ondelete="CASCADE", name="fk_agent_job_events_job"
        ),
        sa.UniqueConstraint("job_id", "sequence", name="uq_agent_job_events_sequence"),
        sa.CheckConstraint("sequence >= 1", name="ck_agent_job_events_sequence_positive"),
        sa.CheckConstraint(
            "kind IN ('queued','running','succeeded','safe_refusal','failed')",
            name="ck_agent_job_events_kind_allowed",
        ),
    )
    op.create_index("ix_agent_job_events_job_sequence", "agent_job_events", ["job_id", "sequence"])


def downgrade() -> None:
    op.drop_index("ix_agent_job_events_job_sequence", table_name="agent_job_events")
    op.drop_table("agent_job_events")
    op.drop_index("ix_agent_jobs_status_created", table_name="agent_jobs")
    op.drop_index("ix_agent_jobs_owner_status_created", table_name="agent_jobs")
    op.drop_table("agent_jobs")
