from uuid import UUID

from sqlalchemy import (
    CheckConstraint,
    Engine,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
    Uuid,
    delete,
    select,
)
from sqlalchemy.orm import Mapped, Session, mapped_column, sessionmaker

from cinegraph.adapters.persistence.base import PersistenceBase
from cinegraph.common.error_messages import GraphErrorMessages
from cinegraph.common.graph_normalization import normalize_graph_identity
from cinegraph.common.identifiers import IdentifierGenerator
from cinegraph.config.graph_claims import GRAPH_CLAIM_EXTRACTION_REVISION
from cinegraph.config.transcript_chunking import TRANSCRIPT_INDEX_REVISION
from cinegraph.domain.enums.enum import RightsStatus, SourceVersionStatus
from cinegraph.domain.models.graph.graph_models import GraphClaim, GraphClaimEvidence, GraphEntity


class GraphEntityRow(PersistenceBase):
    __tablename__ = "graph_entities"
    entity_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    series_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    normalized_key: Mapped[str] = mapped_column(String(256), nullable=False)
    display_name: Mapped[str] = mapped_column(String(256), nullable=False)
    __table_args__ = (
        UniqueConstraint("series_id", "kind", "normalized_key", name="uq_graph_entities_identity"),
        CheckConstraint(
            "kind IN ('character', 'person', 'location', 'organization', 'object', 'event', 'concept')",
            name="ck_graph_entities_kind_allowed",
        ),
        CheckConstraint(
            "length(normalized_key) >= 1 AND length(display_name) >= 1",
            name="ck_graph_entities_names_nonempty",
        ),
        Index("ix_graph_entities_series_kind_key", "series_id", "kind", "normalized_key"),
    )


class GraphEntityAliasRow(PersistenceBase):
    __tablename__ = "graph_entity_aliases"
    alias_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    entity_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("graph_entities.entity_id", name="fk_graph_alias_entity", ondelete="CASCADE"),
        nullable=False,
    )
    alias: Mapped[str] = mapped_column(String(256), nullable=False)
    normalized_alias: Mapped[str] = mapped_column(String(256), nullable=False)
    __table_args__ = (
        UniqueConstraint(
            "entity_id",
            "normalized_alias",
            name="uq_graph_entity_alias_identity",
        ),
        CheckConstraint(
            "length(alias) >= 1 AND length(normalized_alias) >= 1",
            name="ck_graph_entity_aliases_nonempty",
        ),
    )


class GraphClaimRow(PersistenceBase):
    __tablename__ = "graph_claims"
    claim_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    series_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    subject_entity_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("graph_entities.entity_id", name="fk_graph_claim_subject"),
        nullable=False,
    )
    predicate: Mapped[str] = mapped_column(String(96), nullable=False)
    object_entity_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("graph_entities.entity_id", name="fk_graph_claim_object"),
        nullable=False,
    )
    polarity: Mapped[str] = mapped_column(String(16), nullable=False)
    extraction_revision: Mapped[str] = mapped_column(String(128), nullable=False)
    __table_args__ = (
        UniqueConstraint(
            "extraction_revision",
            "series_id",
            "subject_entity_id",
            "predicate",
            "object_entity_id",
            "polarity",
            name="uq_graph_claim_semantics",
        ),
        CheckConstraint(
            "polarity IN ('asserted', 'negated', 'uncertain')",
            name="ck_graph_claims_polarity_allowed",
        ),
        CheckConstraint(
            f"extraction_revision = '{GRAPH_CLAIM_EXTRACTION_REVISION}'",
            name="ck_graph_claims_current_revision",
        ),
        CheckConstraint("length(predicate) >= 1", name="ck_graph_claims_predicate_nonempty"),
        Index(
            "ix_graph_claims_traversal",
            "series_id",
            "subject_entity_id",
            "predicate",
            "object_entity_id",
        ),
    )


class GraphClaimEvidenceRow(PersistenceBase):
    __tablename__ = "graph_claim_evidence"
    evidence_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    claim_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("graph_claims.claim_id", name="fk_graph_evidence_claim", ondelete="CASCADE"),
        nullable=False,
    )
    source_version_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    transcript_chunk_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    series_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    season_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    episode_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    season_number: Mapped[int] = mapped_column(Integer, nullable=False)
    episode_number: Mapped[int] = mapped_column(Integer, nullable=False)
    start_ms: Mapped[int] = mapped_column(Integer, nullable=False)
    end_ms: Mapped[int] = mapped_column(Integer, nullable=False)
    confidence: Mapped[float] = mapped_column(Float, nullable=False)
    transcript_index_revision: Mapped[str] = mapped_column(String(128), nullable=False)
    extraction_revision: Mapped[str] = mapped_column(String(128), nullable=False)
    rights_status: Mapped[str] = mapped_column(String(16), nullable=False)
    source_status: Mapped[str] = mapped_column(String(16), nullable=False)
    review_status: Mapped[str] = mapped_column(String(32), nullable=False)
    __table_args__ = (
        UniqueConstraint(
            "claim_id",
            "source_version_id",
            "transcript_chunk_id",
            name="uq_graph_evidence_source_chunk",
        ),
        CheckConstraint("confidence >= 0 AND confidence <= 1", name="ck_graph_evidence_confidence"),
        CheckConstraint("start_ms >= 0 AND end_ms > start_ms", name="ck_graph_evidence_timing"),
        CheckConstraint(
            "season_number >= 1 AND episode_number >= 1",
            name="ck_graph_evidence_episode_position_positive",
        ),
        CheckConstraint(
            f"transcript_index_revision = '{TRANSCRIPT_INDEX_REVISION}'",
            name="ck_graph_evidence_current_transcript_revision",
        ),
        CheckConstraint(
            f"extraction_revision = '{GRAPH_CLAIM_EXTRACTION_REVISION}'",
            name="ck_graph_evidence_current_extraction_revision",
        ),
        CheckConstraint(
            f"rights_status = '{RightsStatus.ALLOWED.value}'",
            name="ck_graph_evidence_rights_allowed",
        ),
        CheckConstraint(
            f"source_status = '{SourceVersionStatus.ACTIVE.value}'",
            name="ck_graph_evidence_source_active",
        ),
        CheckConstraint(
            "review_status IN ('automated_reviewed', 'hybrid_reviewed', 'reviewed')",
            name="ck_graph_evidence_review_approved",
        ),
        Index(
            "ix_graph_evidence_source_chunk_episode",
            "source_version_id",
            "transcript_chunk_id",
            "episode_id",
            "season_number",
            "episode_number",
        ),
        Index(
            "ix_graph_evidence_visibility",
            "series_id",
            "episode_id",
            "end_ms",
            "rights_status",
            "source_status",
            "review_status",
            "transcript_index_revision",
            "extraction_revision",
        ),
    )


class SqlAlchemyGraphClaimStore:
    def __init__(self, engine: Engine) -> None:
        self._session_factory = sessionmaker(bind=engine, expire_on_commit=False)

    def replace_source_version(
        self,
        new_source_version_id: UUID,
        retired_source_version_id: UUID | None,
        entities: tuple[GraphEntity, ...],
        claims: tuple[GraphClaim, ...],
        evidence: tuple[GraphClaimEvidence, ...],
    ) -> None:
        if not isinstance(new_source_version_id, UUID) or (
            retired_source_version_id is not None
            and not isinstance(retired_source_version_id, UUID)
        ):
            raise ValueError(GraphErrorMessages.REPLACEMENT_INVALID)
        if retired_source_version_id == new_source_version_id:
            raise ValueError(GraphErrorMessages.REPLACEMENT_INVALID)
        self._validate_batch(new_source_version_id, entities, claims, evidence)
        with self._session_factory.begin() as session:
            self._replace(
                session,
                retired_source_version_id,
                entities,
                claims,
                evidence,
            )

    @staticmethod
    def _replace(
        session: Session,
        retired_source_version_id: UUID | None,
        entities: tuple[GraphEntity, ...],
        claims: tuple[GraphClaim, ...],
        evidence: tuple[GraphClaimEvidence, ...],
    ) -> None:
        for entity in entities:
            existing_entity = session.get(GraphEntityRow, entity.entity_id)
            if existing_entity is None:
                session.add(
                    GraphEntityRow(
                        entity_id=entity.entity_id,
                        series_id=entity.series_id,
                        kind=entity.kind.value,
                        normalized_key=entity.normalized_key,
                        display_name=entity.display_name,
                    )
                )
            elif (
                existing_entity.series_id,
                existing_entity.kind,
                existing_entity.normalized_key,
                existing_entity.display_name,
            ) != (
                entity.series_id,
                entity.kind.value,
                entity.normalized_key,
                entity.display_name,
            ):
                raise ValueError(GraphErrorMessages.ENTITY_METADATA_CONFLICT)
            for alias in entity.aliases:
                normalized_alias = normalize_graph_identity(alias)
                alias_id = IdentifierGenerator.graph_entity_alias_id(
                    entity.entity_id,
                    normalized_alias,
                )
                existing_alias = session.get(GraphEntityAliasRow, alias_id)
                if existing_alias is None:
                    session.add(
                        GraphEntityAliasRow(
                            alias_id=alias_id,
                            entity_id=entity.entity_id,
                            alias=alias,
                            normalized_alias=normalized_alias,
                        )
                    )
                elif (
                    existing_alias.entity_id,
                    existing_alias.alias,
                    existing_alias.normalized_alias,
                ) != (entity.entity_id, alias, normalized_alias):
                    raise ValueError(GraphErrorMessages.ALIAS_METADATA_CONFLICT)
        session.flush()
        for claim in claims:
            existing_claim = session.get(GraphClaimRow, claim.claim_id)
            if existing_claim is None:
                session.add(
                    GraphClaimRow(
                        claim_id=claim.claim_id,
                        series_id=claim.series_id,
                        subject_entity_id=claim.subject_entity_id,
                        predicate=claim.predicate,
                        object_entity_id=claim.object_entity_id,
                        polarity=claim.polarity.value,
                        extraction_revision=claim.extraction_revision,
                    )
                )
            elif (
                existing_claim.series_id,
                existing_claim.subject_entity_id,
                existing_claim.predicate,
                existing_claim.object_entity_id,
                existing_claim.polarity,
                existing_claim.extraction_revision,
            ) != (
                claim.series_id,
                claim.subject_entity_id,
                claim.predicate,
                claim.object_entity_id,
                claim.polarity.value,
                claim.extraction_revision,
            ):
                raise ValueError(GraphErrorMessages.CLAIM_METADATA_CONFLICT)
        session.flush()
        for item in evidence:
            existing_evidence = session.get(GraphClaimEvidenceRow, item.evidence_id)
            values = dict(
                evidence_id=item.evidence_id,
                claim_id=item.claim_id,
                source_version_id=item.source_version_id,
                transcript_chunk_id=item.transcript_chunk_id,
                series_id=item.episode.series_id,
                season_id=item.episode.season_id,
                episode_id=item.episode.episode_id,
                season_number=item.episode.position.season_number,
                episode_number=item.episode.position.episode_number,
                start_ms=item.start_ms,
                end_ms=item.end_ms,
                confidence=item.confidence,
                transcript_index_revision=item.transcript_index_revision,
                extraction_revision=item.extraction_revision,
                rights_status=item.rights_status.value,
                source_status=item.source_status.value,
                review_status=item.review_status.value,
            )
            if existing_evidence is None:
                session.add(GraphClaimEvidenceRow(**values))
            elif any(
                getattr(existing_evidence, key) != value
                for key, value in values.items()
                if key != "evidence_id"
            ):
                raise ValueError(GraphErrorMessages.EVIDENCE_METADATA_CONFLICT)
        session.flush()
        if retired_source_version_id is not None:
            session.execute(
                delete(GraphClaimEvidenceRow).where(
                    GraphClaimEvidenceRow.source_version_id == retired_source_version_id
                )
            )
        orphaned = (
            select(GraphClaimRow.claim_id)
            .outerjoin(GraphClaimEvidenceRow)
            .where(GraphClaimEvidenceRow.claim_id.is_(None))
        )
        session.execute(delete(GraphClaimRow).where(GraphClaimRow.claim_id.in_(orphaned)))
        session.flush()

    @staticmethod
    def _validate_batch(
        source_version_id: UUID,
        entities: tuple[GraphEntity, ...],
        claims: tuple[GraphClaim, ...],
        evidence: tuple[GraphClaimEvidence, ...],
    ) -> None:
        if (
            not isinstance(entities, tuple)
            or not isinstance(claims, tuple)
            or not isinstance(evidence, tuple)
            or any(not isinstance(item, GraphEntity) for item in entities)
            or any(not isinstance(item, GraphClaim) for item in claims)
            or any(not isinstance(item, GraphClaimEvidence) for item in evidence)
        ):
            raise ValueError(GraphErrorMessages.STORE_BATCH_INVALID)
        entity_ids = {entity.entity_id for entity in entities}
        if len(entity_ids) != len(entities) or any(
            entity.entity_id
            != IdentifierGenerator.graph_entity_id(
                entity.series_id,
                entity.kind,
                entity.normalized_key,
            )
            for entity in entities
        ):
            raise ValueError(GraphErrorMessages.ENTITY_FIELDS_INVALID)
        entities_by_id = {entity.entity_id: entity for entity in entities}
        claim_ids = {claim.claim_id for claim in claims}
        if len(claim_ids) != len(claims) or any(
            claim.claim_id
            != IdentifierGenerator.graph_claim_id(
                claim.extraction_revision,
                claim.series_id,
                claim.subject_entity_id,
                claim.predicate,
                claim.object_entity_id,
                claim.polarity,
            )
            or claim.subject_entity_id not in entity_ids
            or claim.object_entity_id not in entity_ids
            or entities_by_id[claim.subject_entity_id].series_id != claim.series_id
            or entities_by_id[claim.object_entity_id].series_id != claim.series_id
            for claim in claims
        ):
            raise ValueError(GraphErrorMessages.CLAIM_FIELDS_INVALID)
        referenced_entity_ids = {
            entity_id
            for claim in claims
            for entity_id in (claim.subject_entity_id, claim.object_entity_id)
        }
        if referenced_entity_ids != entity_ids:
            raise ValueError(GraphErrorMessages.STORE_BATCH_INVALID)
        claims_by_id = {claim.claim_id: claim for claim in claims}
        evidence_ids = {item.evidence_id for item in evidence}
        evidence_keys = {
            (item.claim_id, item.source_version_id, item.transcript_chunk_id) for item in evidence
        }
        if (
            len(evidence_ids) != len(evidence)
            or any(
                item.evidence_id
                != IdentifierGenerator.graph_evidence_id(
                    item.claim_id,
                    item.source_version_id,
                    item.transcript_chunk_id,
                )
                or item.claim_id not in claim_ids
                or item.source_version_id != source_version_id
                or item.episode.series_id != claims_by_id[item.claim_id].series_id
                or item.extraction_revision != claims_by_id[item.claim_id].extraction_revision
                for item in evidence
            )
            or len(evidence_keys) != len(evidence)
        ):
            raise ValueError(GraphErrorMessages.EVIDENCE_FIELDS_INVALID)
        if {item.claim_id for item in evidence} != claim_ids:
            raise ValueError(GraphErrorMessages.STORE_BATCH_INVALID)
