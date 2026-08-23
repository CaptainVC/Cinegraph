from collections import defaultdict
from uuid import UUID

from cinegraph.application.models.graph_claim_extraction import (
    ExtractAndReplaceGraphClaimsCommand,
    ExtractAndReplaceGraphClaimsResult,
    ExtractedEntityReference,
    ExtractedGraphClaim,
)
from cinegraph.common.error_messages import GraphErrorMessages
from cinegraph.common.graph_normalization import (
    normalize_graph_display,
    normalize_graph_identity,
    normalize_graph_predicate,
)
from cinegraph.common.identifiers import IdentifierGenerator
from cinegraph.config.graph_claims import (
    DEFAULT_GRAPH_CLAIM_EXTRACTION_CONFIGURATION,
    GraphClaimExtractionConfiguration,
)
from cinegraph.config.transcript_chunking import TRANSCRIPT_INDEX_REVISION
from cinegraph.domain.enums.enum import (
    GraphClaimPolarity,
    GraphEntityKind,
    RightsStatus,
    SourceVersionStatus,
)
from cinegraph.domain.exceptions.errors import InvalidModelError
from cinegraph.domain.models.graph.graph_models import GraphClaim, GraphClaimEvidence, GraphEntity
from cinegraph.domain.models.source.review_status import is_source_version_approved
from cinegraph.domain.models.source.source_version import SourceVersion
from cinegraph.domain.models.transcript.transcript_retrieval_chunk import TranscriptRetrievalChunk
from cinegraph.ports.graph.graph_claim_extractor import GraphClaimExtractor
from cinegraph.ports.graph.graph_claim_store import GraphClaimStore


class ExtractAndReplaceGraphClaimsService:
    def __init__(
        self,
        extractor: GraphClaimExtractor,
        store: GraphClaimStore,
        configuration: GraphClaimExtractionConfiguration = DEFAULT_GRAPH_CLAIM_EXTRACTION_CONFIGURATION,
    ) -> None:
        self._extractor = extractor
        self._store = store
        self._configuration = configuration

    def execute(
        self, command: ExtractAndReplaceGraphClaimsCommand
    ) -> ExtractAndReplaceGraphClaimsResult:
        if not isinstance(command, ExtractAndReplaceGraphClaimsCommand):
            raise InvalidModelError(GraphErrorMessages.COMMAND_INVALID)
        if not isinstance(command.source_version, SourceVersion):
            raise InvalidModelError(GraphErrorMessages.SOURCE_NOT_GOVERNED)
        source = command.source_version
        if (
            source.rights_status is not RightsStatus.ALLOWED
            or source.status is not SourceVersionStatus.ACTIVE
            or not is_source_version_approved(source.review_status)
        ):
            raise InvalidModelError(GraphErrorMessages.SOURCE_NOT_GOVERNED)
        if source.parent_source_version_id == source.source_version_id:
            raise InvalidModelError(GraphErrorMessages.REPLACEMENT_INVALID)
        chunks = command.chunks
        if not isinstance(chunks, tuple) or len(chunks) > self._configuration.max_chunks:
            raise InvalidModelError(GraphErrorMessages.CHUNK_LIMIT_EXCEEDED)
        self._validate_chunks(chunks, source.source_version_id)
        series_id = chunks[0].episode.series_id if chunks else None
        candidates: list[ExtractedGraphClaim] = []
        for offset in range(0, len(chunks), self._configuration.batch_size):
            batch = chunks[offset : offset + self._configuration.batch_size]
            extracted = self._extractor.extract(batch)
            if not isinstance(extracted, tuple):
                raise InvalidModelError(GraphErrorMessages.BATCH_OUTPUT_INVALID)
            batch_chunks = {chunk.chunk_id: chunk for chunk in batch}
            for candidate in extracted:
                self._validate_candidate(candidate, batch_chunks)
            candidates.extend(extracted)
            if len(candidates) > self._configuration.max_candidates:
                raise InvalidModelError(GraphErrorMessages.BATCH_OUTPUT_INVALID)
        entities_by_key: dict[tuple[GraphEntityKind, str], GraphEntity] = {}
        aliases: dict[tuple[GraphEntityKind, str], set[str]] = defaultdict(set)
        claim_map: dict[tuple[UUID, str, UUID, GraphClaimPolarity], GraphClaim] = {}
        evidence_map: dict[UUID, GraphClaimEvidence] = {}
        chunks_by_id = {chunk.chunk_id: chunk for chunk in chunks}
        for candidate in candidates:
            self._validate_candidate(candidate, chunks_by_id)
            if series_id is None:
                raise InvalidModelError(GraphErrorMessages.CHUNKS_INVALID)
            subject = self._entity(candidate.subject, series_id)
            object_entity = self._entity(candidate.object, series_id)
            for entity, reference in (
                (subject, candidate.subject),
                (object_entity, candidate.object),
            ):
                key = (entity.kind, entity.normalized_key)
                aliases[key].update((reference.name, *reference.aliases))
                existing = entities_by_key.get(key)
                if existing is None:
                    entities_by_key[key] = entity
                elif (normalize_graph_identity(entity.display_name), entity.display_name) < (
                    normalize_graph_identity(existing.display_name),
                    existing.display_name,
                ):
                    entities_by_key[key] = GraphEntity(
                        existing.entity_id,
                        existing.series_id,
                        existing.kind,
                        existing.normalized_key,
                        entity.display_name,
                        existing.aliases,
                    )
            predicate = normalize_graph_predicate(candidate.predicate)
            if len(predicate) > self._configuration.max_predicate_length:
                raise InvalidModelError(GraphErrorMessages.CLAIM_PREDICATE_INVALID)
            claim_id = IdentifierGenerator.graph_claim_id(
                self._configuration.revision,
                series_id,
                subject.entity_id,
                predicate,
                object_entity.entity_id,
                candidate.polarity,
            )
            claim_key = (subject.entity_id, predicate, object_entity.entity_id, candidate.polarity)
            claim = claim_map.setdefault(
                claim_key,
                GraphClaim(
                    claim_id,
                    series_id,
                    subject.entity_id,
                    predicate,
                    object_entity.entity_id,
                    candidate.polarity,
                    self._configuration.revision,
                ),
            )
            for chunk_id in candidate.evidence_chunk_ids:
                chunk = chunks_by_id[chunk_id]
                evidence_id = IdentifierGenerator.graph_evidence_id(
                    claim.claim_id, source.source_version_id, chunk_id
                )
                old = evidence_map.get(evidence_id)
                if old is None or candidate.confidence > old.confidence:
                    evidence_map[evidence_id] = GraphClaimEvidence(
                        evidence_id,
                        claim.claim_id,
                        source.source_version_id,
                        chunk_id,
                        chunk.episode,
                        chunk.start_ms,
                        chunk.end_ms,
                        candidate.confidence,
                        chunk.index_revision,
                        self._configuration.revision,
                        source.rights_status,
                        source.status,
                        source.review_status,
                    )
        final_entities_list: list[GraphEntity] = []
        for key, entity in entities_by_key.items():
            alias_values = sorted(
                (entity.display_name, *(alias.strip() for alias in aliases[key])),
                key=lambda value: (
                    normalize_graph_identity(value),
                    value != entity.display_name,
                    value,
                ),
            )
            seen_alias_keys: set[str] = set()
            unique_aliases: list[str] = []
            for alias in alias_values:
                alias_key = normalize_graph_identity(alias)
                if alias_key not in seen_alias_keys:
                    seen_alias_keys.add(alias_key)
                    unique_aliases.append(alias)
            normalized_aliases = tuple(unique_aliases)
            if len(normalized_aliases) > self._configuration.max_aliases:
                raise InvalidModelError(GraphErrorMessages.ENTITY_ALIASES_INVALID)
            final_entities_list.append(
                GraphEntity(
                    entity.entity_id,
                    entity.series_id,
                    entity.kind,
                    entity.normalized_key,
                    entity.display_name,
                    normalized_aliases,
                )
            )
        final_entities = tuple(sorted(final_entities_list, key=lambda item: str(item.entity_id)))
        final_claims = tuple(sorted(claim_map.values(), key=lambda claim: str(claim.claim_id)))
        final_evidence = tuple(
            sorted(evidence_map.values(), key=lambda item: str(item.evidence_id))
        )
        self._store.replace_source_version(
            source.source_version_id,
            source.parent_source_version_id,
            final_entities,
            final_claims,
            final_evidence,
        )
        return ExtractAndReplaceGraphClaimsResult(
            len(chunks),
            len(candidates),
            len(final_entities),
            len(final_claims),
            len(final_evidence),
        )

    @staticmethod
    def _validate_chunks(chunks: tuple[TranscriptRetrievalChunk, ...], source_id: UUID) -> None:
        seen: set[UUID] = set()
        series: UUID | None = None
        for value in chunks:
            if (
                not isinstance(value, TranscriptRetrievalChunk)
                or value.source_version_id != source_id
                or value.rights_status is not RightsStatus.ALLOWED
                or value.index_revision != TRANSCRIPT_INDEX_REVISION
                or value.chunk_id in seen
            ):
                raise InvalidModelError(GraphErrorMessages.CHUNKS_INVALID)
            if series is None:
                series = value.episode.series_id
            elif value.episode.series_id != series:
                raise InvalidModelError(GraphErrorMessages.CHUNKS_INVALID)
            seen.add(value.chunk_id)

    def _entity(self, reference: ExtractedEntityReference, series_id: UUID) -> GraphEntity:
        if (
            not isinstance(reference, ExtractedEntityReference)
            or not isinstance(reference.name, str)
            or not reference.name.strip()
            or not isinstance(reference.aliases, tuple)
        ):
            raise InvalidModelError(GraphErrorMessages.ENTITY_FIELDS_INVALID)
        if len(reference.aliases) > self._configuration.max_aliases:
            # The canonical display name must also be represented as an alias.
            raise InvalidModelError(GraphErrorMessages.ENTITY_ALIASES_INVALID)
        try:
            display_name = normalize_graph_display(reference.name)
            display_aliases = tuple(normalize_graph_display(alias) for alias in reference.aliases)
        except ValueError as error:
            raise InvalidModelError(GraphErrorMessages.ENTITY_NAME_INVALID) from error
        if len(display_name) > self._configuration.max_name_length or any(
            len(alias) > self._configuration.max_name_length for alias in display_aliases
        ):
            raise InvalidModelError(GraphErrorMessages.ENTITY_NAME_INVALID)
        normalized = normalize_graph_identity(display_name)
        entity_id = IdentifierGenerator.graph_entity_id(series_id, reference.kind, normalized)
        alias_values = sorted(
            set((display_name,) + display_aliases),
            key=lambda value: (normalize_graph_identity(value), value),
        )
        alias_keys: set[str] = set()
        unique_aliases: list[str] = []
        for alias in alias_values:
            alias_key = normalize_graph_identity(alias)
            if alias_key not in alias_keys:
                alias_keys.add(alias_key)
                unique_aliases.append(alias)
        aliases = tuple(unique_aliases)
        if len(aliases) > self._configuration.max_aliases:
            raise InvalidModelError(GraphErrorMessages.ENTITY_ALIASES_INVALID)
        return GraphEntity(
            entity_id,
            series_id,
            reference.kind,
            normalized,
            display_name,
            aliases,
        )

    @staticmethod
    def _validate_candidate(
        candidate: ExtractedGraphClaim, chunks: dict[UUID, TranscriptRetrievalChunk]
    ) -> None:
        if (
            not isinstance(candidate, ExtractedGraphClaim)
            or not isinstance(candidate.evidence_chunk_ids, tuple)
            or not candidate.evidence_chunk_ids
            or len(set(candidate.evidence_chunk_ids)) != len(candidate.evidence_chunk_ids)
            or any(chunk_id not in chunks for chunk_id in candidate.evidence_chunk_ids)
        ):
            raise InvalidModelError(GraphErrorMessages.UNKNOWN_EVIDENCE)
