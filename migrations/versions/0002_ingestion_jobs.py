"""Create durable governed ingestion jobs and append-only events."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0002_ingestion_jobs"
down_revision: str | None = "0001_identity_schema"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    uuid_type = sa.Uuid(as_uuid=True)
    op.create_table(
        "ingestion_jobs",
        sa.Column("job_id", uuid_type, primary_key=True),
        sa.Column("idempotency_key", sa.String(length=64), nullable=False),
        sa.Column("kind", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("series_id", uuid_type, nullable=False),
        sa.Column("season_number", sa.Integer(), nullable=True),
        sa.Column("episode_number", sa.Integer(), nullable=True),
        sa.Column("source_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("pipeline_revision", sa.String(length=128), nullable=False),
        sa.Column("priority", sa.Integer(), nullable=False),
        sa.Column("scheduled_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("max_attempts", sa.Integer(), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("lease_owner", sa.String(length=128), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error_code", sa.String(length=64), nullable=True),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("idempotency_key", name="uq_ingestion_jobs_idempotency_key"),
        sa.CheckConstraint(
            "kind IN ('speaker_review', 'transcript_ingestion', 'vector_index', 'episode_summary', 'series_metadata', 'subtitle_alignment')",
            name="ck_ingestion_jobs_kind_allowed",
        ),
        sa.CheckConstraint(
            "status IN ('pending', 'running', 'succeeded', 'failed', 'cancelled')",
            name="ck_ingestion_jobs_status_allowed",
        ),
        sa.CheckConstraint(
            "length(idempotency_key) = 64 AND length(source_fingerprint) = 64",
            name="ck_ingestion_jobs_sha_lengths",
        ),
        sa.CheckConstraint(
            "season_number IS NULL OR season_number >= 1", name="ck_ingestion_jobs_season_positive"
        ),
        sa.CheckConstraint(
            "episode_number IS NULL OR episode_number >= 1",
            name="ck_ingestion_jobs_episode_positive",
        ),
        sa.CheckConstraint(
            "episode_number IS NULL OR season_number IS NOT NULL",
            name="ck_ingestion_jobs_episode_requires_season",
        ),
        sa.CheckConstraint(
            "priority >= 0 AND priority <= 100", name="ck_ingestion_jobs_priority_bounds"
        ),
        sa.CheckConstraint(
            "max_attempts >= 1 AND max_attempts <= 20", name="ck_ingestion_jobs_max_attempts_bounds"
        ),
        sa.CheckConstraint(
            "attempts >= 0 AND attempts <= max_attempts", name="ck_ingestion_jobs_attempt_bounds"
        ),
        sa.CheckConstraint(
            "status <> 'running' OR attempts >= 1", name="ck_ingestion_jobs_running_attempts"
        ),
        sa.CheckConstraint(
            "status <> 'running' OR started_at IS NOT NULL",
            name="ck_ingestion_jobs_running_started",
        ),
        sa.CheckConstraint(
            "status NOT IN ('succeeded', 'failed') OR attempts >= 1",
            name="ck_ingestion_jobs_terminal_attempts",
        ),
        sa.CheckConstraint(
            "status NOT IN ('succeeded', 'failed') OR started_at IS NOT NULL",
            name="ck_ingestion_jobs_terminal_started",
        ),
        sa.CheckConstraint(
            "(status = 'running' AND lease_owner IS NOT NULL AND lease_expires_at IS NOT NULL) "
            "OR (status <> 'running' AND lease_owner IS NULL AND lease_expires_at IS NULL)",
            name="ck_ingestion_jobs_lease_coherence",
        ),
        sa.CheckConstraint(
            "status NOT IN ('succeeded', 'failed', 'cancelled') OR finished_at IS NOT NULL",
            name="ck_ingestion_jobs_terminal_finished",
        ),
        sa.CheckConstraint(
            "status IN ('succeeded', 'failed', 'cancelled') OR finished_at IS NULL",
            name="ck_ingestion_jobs_nonterminal_unfinished",
        ),
        sa.CheckConstraint(
            "status <> 'succeeded' OR last_error_code IS NULL",
            name="ck_ingestion_jobs_success_without_error",
        ),
        sa.CheckConstraint(
            "status <> 'failed' OR last_error_code IS NOT NULL",
            name="ck_ingestion_jobs_failed_error_required",
        ),
        sa.CheckConstraint(
            "status <> 'cancelled' OR last_error_code IS NULL",
            name="ck_ingestion_jobs_cancelled_without_error",
        ),
        sa.CheckConstraint(
            "last_error_code IS NULL OR status IN ('pending', 'failed')",
            name="ck_ingestion_jobs_error_status_coherence",
        ),
        sa.CheckConstraint(
            "next_attempt_at IS NULL OR status = 'pending'",
            name="ck_ingestion_jobs_next_attempt_status_coherence",
        ),
        sa.CheckConstraint(
            "last_error_code IS NULL OR last_error_code IN ('lease_expired', 'lease_expired_max_attempts', 'source_invalid', 'alignment_failed', 'speaker_review_failed', 'transcript_ingestion_failed', 'vector_index_failed', 'episode_summary_failed', 'series_metadata_failed', 'unknown_retryable')",
            name="ck_ingestion_jobs_error_code_allowed",
        ),
        sa.CheckConstraint(
            "status <> 'pending' OR ((next_attempt_at IS NULL AND last_error_code IS NULL) OR (next_attempt_at IS NOT NULL AND last_error_code IS NOT NULL))",
            name="ck_ingestion_jobs_pending_retry_pair",
        ),
        sa.CheckConstraint(
            "status <> 'pending' OR (attempts = 0 AND next_attempt_at IS NULL AND last_error_code IS NULL) OR (attempts > 0 AND next_attempt_at IS NOT NULL AND last_error_code IS NOT NULL)",
            name="ck_ingestion_jobs_pending_attempt_coherence",
        ),
        sa.CheckConstraint(
            "next_attempt_at IS NULL OR next_attempt_at >= scheduled_at",
            name="ck_ingestion_jobs_pending_schedule",
        ),
        sa.CheckConstraint(
            "started_at IS NULL OR started_at >= created_at",
            name="ck_ingestion_jobs_started_after_created",
        ),
        sa.CheckConstraint(
            "finished_at IS NULL OR finished_at >= created_at",
            name="ck_ingestion_jobs_finished_after_created",
        ),
        sa.CheckConstraint(
            "finished_at IS NULL OR started_at IS NULL OR finished_at >= started_at",
            name="ck_ingestion_jobs_finished_after_started",
        ),
    )
    op.create_index(
        "ix_ingestion_jobs_claim",
        "ingestion_jobs",
        ["status", "priority", "scheduled_at", "next_attempt_at", "created_at"],
    )
    op.create_index(
        "ix_ingestion_jobs_lease_expiry", "ingestion_jobs", ["status", "lease_expires_at"]
    )
    op.create_index(
        "ix_ingestion_jobs_scope",
        "ingestion_jobs",
        ["series_id", "season_number", "episode_number"],
    )
    op.create_table(
        "ingestion_job_events",
        sa.Column("event_id", uuid_type, primary_key=True),
        sa.Column("job_id", uuid_type, nullable=False),
        sa.Column("sequence_number", sa.Integer(), nullable=False),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("attempt", sa.Integer(), nullable=False),
        sa.Column("worker_id", sa.String(length=128), nullable=True),
        sa.Column("error_code", sa.String(length=64), nullable=True),
        sa.ForeignKeyConstraint(
            ["job_id"], ["ingestion_jobs.job_id"], name="fk_ingestion_job_events_job"
        ),
        sa.UniqueConstraint("job_id", "sequence_number", name="uq_ingestion_job_events_sequence"),
        sa.CheckConstraint(
            "sequence_number >= 1", name="ck_ingestion_job_events_sequence_positive"
        ),
        sa.CheckConstraint("attempt >= 0", name="ck_ingestion_job_events_attempt_nonnegative"),
        sa.CheckConstraint(
            "kind IN ('enqueued', 'claimed', 'heartbeat', 'retried', 'succeeded', 'failed', 'cancelled', 'reclaimed')",
            name="ck_ingestion_job_events_kind_allowed",
        ),
        sa.CheckConstraint(
            "kind NOT IN ('claimed', 'heartbeat', 'succeeded', 'failed', 'retried') OR worker_id IS NOT NULL",
            name="ck_ingestion_job_events_worker_required",
        ),
        sa.CheckConstraint(
            "kind IN ('retried', 'failed', 'reclaimed') AND error_code IS NOT NULL OR kind NOT IN ('retried', 'failed', 'reclaimed') AND error_code IS NULL",
            name="ck_ingestion_job_events_error_coherence",
        ),
        sa.CheckConstraint(
            "error_code IS NULL OR error_code IN ('lease_expired', 'lease_expired_max_attempts', 'source_invalid', 'alignment_failed', 'speaker_review_failed', 'transcript_ingestion_failed', 'vector_index_failed', 'episode_summary_failed', 'series_metadata_failed', 'unknown_retryable')",
            name="ck_ingestion_job_events_error_code_allowed",
        ),
    )
    op.create_index(
        "ix_ingestion_job_events_job_order", "ingestion_job_events", ["job_id", "sequence_number"]
    )


def downgrade() -> None:
    op.drop_index("ix_ingestion_job_events_job_order", table_name="ingestion_job_events")
    op.drop_table("ingestion_job_events")
    op.drop_index("ix_ingestion_jobs_scope", table_name="ingestion_jobs")
    op.drop_index("ix_ingestion_jobs_lease_expiry", table_name="ingestion_jobs")
    op.drop_index("ix_ingestion_jobs_claim", table_name="ingestion_jobs")
    op.drop_table("ingestion_jobs")
