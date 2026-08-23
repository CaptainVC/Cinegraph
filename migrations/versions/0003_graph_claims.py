"""Add governed relational graph claims and evidence."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0003_graph_claims"
down_revision: str | None = "0002_ingestion_jobs"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None

INGESTION_KIND_CHECK_V2 = (
    "kind IN ('speaker_review', 'transcript_ingestion', 'vector_index', "
    "'episode_summary', 'series_metadata', 'subtitle_alignment', "
    "'graph_claim_extraction')"
)
INGESTION_KIND_CHECK_V1 = (
    "kind IN ('speaker_review', 'transcript_ingestion', 'vector_index', "
    "'episode_summary', 'series_metadata', 'subtitle_alignment')"
)
INGESTION_ERROR_CHECK_V2 = (
    "last_error_code IS NULL OR last_error_code IN ('lease_expired', "
    "'lease_expired_max_attempts', 'source_invalid', 'alignment_failed', "
    "'speaker_review_failed', 'transcript_ingestion_failed', "
    "'vector_index_failed', 'episode_summary_failed', "
    "'series_metadata_failed', 'graph_claim_extraction_failed', "
    "'unknown_retryable')"
)
INGESTION_ERROR_CHECK_V1 = INGESTION_ERROR_CHECK_V2.replace(
    "'graph_claim_extraction_failed', ",
    "",
)
INGESTION_EVENT_ERROR_CHECK_V2 = INGESTION_ERROR_CHECK_V2.replace(
    "last_error_code",
    "error_code",
)
INGESTION_EVENT_ERROR_CHECK_V1 = INGESTION_ERROR_CHECK_V1.replace(
    "last_error_code",
    "error_code",
)


def upgrade() -> None:
    uuid_type = sa.Uuid(as_uuid=True)
    op.create_table(
        "graph_entities",
        sa.Column("entity_id", uuid_type, primary_key=True),
        sa.Column("series_id", uuid_type, nullable=False),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("normalized_key", sa.String(length=256), nullable=False),
        sa.Column("display_name", sa.String(length=256), nullable=False),
        sa.UniqueConstraint(
            "series_id",
            "kind",
            "normalized_key",
            name="uq_graph_entities_identity",
        ),
        sa.CheckConstraint(
            "kind IN ('character', 'person', 'location', 'organization', "
            "'object', 'event', 'concept')",
            name="ck_graph_entities_kind_allowed",
        ),
        sa.CheckConstraint(
            "length(normalized_key) >= 1 AND length(display_name) >= 1",
            name="ck_graph_entities_names_nonempty",
        ),
    )
    op.create_index(
        "ix_graph_entities_series_kind_key",
        "graph_entities",
        ["series_id", "kind", "normalized_key"],
    )
    op.create_table(
        "graph_entity_aliases",
        sa.Column("alias_id", uuid_type, primary_key=True),
        sa.Column("entity_id", uuid_type, nullable=False),
        sa.Column("alias", sa.String(length=256), nullable=False),
        sa.Column("normalized_alias", sa.String(length=256), nullable=False),
        sa.ForeignKeyConstraint(
            ["entity_id"],
            ["graph_entities.entity_id"],
            name="fk_graph_alias_entity",
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint(
            "entity_id",
            "normalized_alias",
            name="uq_graph_entity_alias_identity",
        ),
        sa.CheckConstraint(
            "length(alias) >= 1 AND length(normalized_alias) >= 1",
            name="ck_graph_entity_aliases_nonempty",
        ),
    )
    op.create_table(
        "graph_claims",
        sa.Column("claim_id", uuid_type, primary_key=True),
        sa.Column("series_id", uuid_type, nullable=False),
        sa.Column("subject_entity_id", uuid_type, nullable=False),
        sa.Column("predicate", sa.String(length=96), nullable=False),
        sa.Column("object_entity_id", uuid_type, nullable=False),
        sa.Column("polarity", sa.String(length=16), nullable=False),
        sa.Column("extraction_revision", sa.String(length=128), nullable=False),
        sa.ForeignKeyConstraint(
            ["subject_entity_id"],
            ["graph_entities.entity_id"],
            name="fk_graph_claim_subject",
        ),
        sa.ForeignKeyConstraint(
            ["object_entity_id"],
            ["graph_entities.entity_id"],
            name="fk_graph_claim_object",
        ),
        sa.UniqueConstraint(
            "extraction_revision",
            "series_id",
            "subject_entity_id",
            "predicate",
            "object_entity_id",
            "polarity",
            name="uq_graph_claim_semantics",
        ),
        sa.CheckConstraint(
            "polarity IN ('asserted', 'negated', 'uncertain')",
            name="ck_graph_claims_polarity_allowed",
        ),
        sa.CheckConstraint(
            "extraction_revision = 'graph-claim-v1'",
            name="ck_graph_claims_current_revision",
        ),
        sa.CheckConstraint(
            "length(predicate) >= 1",
            name="ck_graph_claims_predicate_nonempty",
        ),
    )
    op.create_index(
        "ix_graph_claims_traversal",
        "graph_claims",
        ["series_id", "subject_entity_id", "predicate", "object_entity_id"],
    )
    op.create_table(
        "graph_claim_evidence",
        sa.Column("evidence_id", uuid_type, primary_key=True),
        sa.Column("claim_id", uuid_type, nullable=False),
        sa.Column("source_version_id", uuid_type, nullable=False),
        sa.Column("transcript_chunk_id", uuid_type, nullable=False),
        sa.Column("series_id", uuid_type, nullable=False),
        sa.Column("season_id", uuid_type, nullable=False),
        sa.Column("episode_id", uuid_type, nullable=False),
        sa.Column("season_number", sa.Integer(), nullable=False),
        sa.Column("episode_number", sa.Integer(), nullable=False),
        sa.Column("start_ms", sa.Integer(), nullable=False),
        sa.Column("end_ms", sa.Integer(), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("transcript_index_revision", sa.String(length=128), nullable=False),
        sa.Column("extraction_revision", sa.String(length=128), nullable=False),
        sa.Column("rights_status", sa.String(length=16), nullable=False),
        sa.Column("source_status", sa.String(length=16), nullable=False),
        sa.Column("review_status", sa.String(length=32), nullable=False),
        sa.ForeignKeyConstraint(
            ["claim_id"],
            ["graph_claims.claim_id"],
            name="fk_graph_evidence_claim",
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint(
            "claim_id",
            "source_version_id",
            "transcript_chunk_id",
            name="uq_graph_evidence_source_chunk",
        ),
        sa.CheckConstraint(
            "confidence >= 0 AND confidence <= 1",
            name="ck_graph_evidence_confidence",
        ),
        sa.CheckConstraint(
            "start_ms >= 0 AND end_ms > start_ms",
            name="ck_graph_evidence_timing",
        ),
        sa.CheckConstraint(
            "season_number >= 1 AND episode_number >= 1",
            name="ck_graph_evidence_episode_position_positive",
        ),
        sa.CheckConstraint(
            "transcript_index_revision = 'transcript-chunk-v1'",
            name="ck_graph_evidence_current_transcript_revision",
        ),
        sa.CheckConstraint(
            "extraction_revision = 'graph-claim-v1'",
            name="ck_graph_evidence_current_extraction_revision",
        ),
        sa.CheckConstraint(
            "rights_status = 'allowed'",
            name="ck_graph_evidence_rights_allowed",
        ),
        sa.CheckConstraint(
            "source_status = 'active'",
            name="ck_graph_evidence_source_active",
        ),
        sa.CheckConstraint(
            "review_status IN ('automated_reviewed', 'hybrid_reviewed', 'reviewed')",
            name="ck_graph_evidence_review_approved",
        ),
    )
    op.create_index(
        "ix_graph_evidence_source_chunk_episode",
        "graph_claim_evidence",
        [
            "source_version_id",
            "transcript_chunk_id",
            "episode_id",
            "season_number",
            "episode_number",
        ],
    )
    op.create_index(
        "ix_graph_evidence_visibility",
        "graph_claim_evidence",
        [
            "series_id",
            "episode_id",
            "end_ms",
            "rights_status",
            "source_status",
            "review_status",
            "transcript_index_revision",
            "extraction_revision",
        ],
    )
    _replace_ingestion_checks(
        INGESTION_KIND_CHECK_V2,
        INGESTION_ERROR_CHECK_V2,
        INGESTION_EVENT_ERROR_CHECK_V2,
    )


def _replace_ingestion_checks(
    kind_check: str,
    error_check: str,
    event_error_check: str,
) -> None:
    if op.get_bind().dialect.name == "sqlite":
        with op.batch_alter_table("ingestion_jobs", recreate="always") as batch:
            batch.drop_constraint("ck_ingestion_jobs_kind_allowed", type_="check")
            batch.create_check_constraint("ck_ingestion_jobs_kind_allowed", kind_check)
            batch.drop_constraint("ck_ingestion_jobs_error_code_allowed", type_="check")
            batch.create_check_constraint(
                "ck_ingestion_jobs_error_code_allowed",
                error_check,
            )
        with op.batch_alter_table("ingestion_job_events", recreate="always") as batch:
            batch.drop_constraint(
                "ck_ingestion_job_events_error_code_allowed",
                type_="check",
            )
            batch.create_check_constraint(
                "ck_ingestion_job_events_error_code_allowed",
                event_error_check,
            )
        return
    op.drop_constraint(
        "ck_ingestion_jobs_kind_allowed",
        "ingestion_jobs",
        type_="check",
    )
    op.create_check_constraint(
        "ck_ingestion_jobs_kind_allowed",
        "ingestion_jobs",
        kind_check,
    )
    op.drop_constraint(
        "ck_ingestion_jobs_error_code_allowed",
        "ingestion_jobs",
        type_="check",
    )
    op.create_check_constraint(
        "ck_ingestion_jobs_error_code_allowed",
        "ingestion_jobs",
        error_check,
    )
    op.drop_constraint(
        "ck_ingestion_job_events_error_code_allowed",
        "ingestion_job_events",
        type_="check",
    )
    op.create_check_constraint(
        "ck_ingestion_job_events_error_code_allowed",
        "ingestion_job_events",
        event_error_check,
    )


def downgrade() -> None:
    _replace_ingestion_checks(
        INGESTION_KIND_CHECK_V1,
        INGESTION_ERROR_CHECK_V1,
        INGESTION_EVENT_ERROR_CHECK_V1,
    )
    op.drop_index("ix_graph_evidence_visibility", table_name="graph_claim_evidence")
    op.drop_index(
        "ix_graph_evidence_source_chunk_episode",
        table_name="graph_claim_evidence",
    )
    op.drop_table("graph_claim_evidence")
    op.drop_index("ix_graph_claims_traversal", table_name="graph_claims")
    op.drop_table("graph_claims")
    op.drop_table("graph_entity_aliases")
    op.drop_index("ix_graph_entities_series_kind_key", table_name="graph_entities")
    op.drop_table("graph_entities")
