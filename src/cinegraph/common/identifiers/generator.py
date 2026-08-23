from uuid import UUID, uuid4, uuid5

from cinegraph.common.error_messages import GraphErrorMessages
from cinegraph.common.graph_normalization import normalize_graph_identity, normalize_graph_predicate
from cinegraph.common.identifiers.templates import IdentifierTemplates
from cinegraph.domain.enums.enum import GraphClaimPolarity, GraphEntityKind, Language


class IdentifierGenerator:
    @staticmethod
    # Generate a random UUID for identifiers without a stable source key.
    def new_id() -> UUID:
        return uuid4()

    @staticmethod
    # Generate a stable UUID from a source document and content hash.
    def source_version_id(
        source_document_id: UUID,
        content_hash: str,
    ) -> UUID:
        return uuid5(
            IdentifierTemplates.NAMESPACE,
            IdentifierTemplates.SOURCE_VERSION.format(
                source_document_id=source_document_id,
                content_hash=content_hash,
            ),
        )

    @staticmethod
    # Generate a stable UUID for one episode transcript source and origin.
    def transcript_source_document_id(
        episode_id: UUID,
        language: Language,
        origin: str,
    ) -> UUID:
        return uuid5(
            IdentifierTemplates.NAMESPACE,
            IdentifierTemplates.TRANSCRIPT_SOURCE_DOCUMENT.format(
                episode_id=episode_id,
                language=language.value,
                origin=origin.casefold(),
            ),
        )

    @staticmethod
    # Generate a stable series-scoped UUID from a speaker name.
    def speaker_id(
        series_id: UUID,
        speaker_name: str,
    ) -> UUID:
        return uuid5(
            IdentifierTemplates.NAMESPACE,
            IdentifierTemplates.SPEAKER.format(
                series_id=series_id,
                speaker_name=speaker_name.casefold(),
            ),
        )

    @staticmethod
    # Generate a stable UUID from a transcript version, cue timing, and text.
    def transcript_segment_id(
        source_version_id: UUID,
        episode_id: UUID,
        cue_number: int,
        start_ms: int,
        end_ms: int,
        text: str,
    ) -> UUID:
        return uuid5(
            IdentifierTemplates.NAMESPACE,
            IdentifierTemplates.TRANSCRIPT_SEGMENT.format(
                source_version_id=source_version_id,
                episode_id=episode_id,
                cue_number=cue_number,
                start_ms=start_ms,
                end_ms=end_ms,
                text=text,
            ),
        )

    @staticmethod
    def transcript_chunk_id(
        revision: str,
        source_version_id: UUID,
        series_id: UUID,
        season_id: UUID,
        episode_id: UUID,
        segment_ids: tuple[UUID, ...],
    ) -> UUID:
        return uuid5(
            IdentifierTemplates.NAMESPACE,
            IdentifierTemplates.TRANSCRIPT_CHUNK.format(
                revision=revision,
                source_version_id=source_version_id,
                series_id=series_id,
                season_id=season_id,
                episode_id=episode_id,
                segment_ids=",".join(str(identifier) for identifier in segment_ids),
            ),
        )

    @staticmethod
    # Generate a stable UUID from a summary version, episode, and language.
    def episode_summary_id(
        source_version_id: UUID,
        episode_id: UUID,
        language: Language,
    ) -> UUID:
        return uuid5(
            IdentifierTemplates.NAMESPACE,
            IdentifierTemplates.EPISODE_SUMMARY.format(
                source_version_id=source_version_id,
                episode_id=episode_id,
                language=language.value,
            ),
        )

    @staticmethod
    # Generate the stable identifier used for an episode summary document.
    def episode_summary_document_id(
        source_version_id: UUID,
        episode_id: UUID,
        language: Language,
    ) -> UUID:
        return IdentifierGenerator.episode_summary_id(
            source_version_id,
            episode_id,
            language,
        )

    @staticmethod
    # Generate a stable UUID for an episode summary source and its origin.
    def episode_summary_source_document_id(
        episode_id: UUID,
        language: Language,
        origin: str,
    ) -> UUID:
        return uuid5(
            IdentifierTemplates.NAMESPACE,
            IdentifierTemplates.EPISODE_SUMMARY_SOURCE_DOCUMENT.format(
                episode_id=episode_id,
                language=language.value,
                origin=origin.casefold(),
            ),
        )

    @staticmethod
    def series_metadata_source_document_id(series_id: UUID, origin: str) -> UUID:
        return uuid5(
            IdentifierTemplates.NAMESPACE,
            IdentifierTemplates.SERIES_METADATA_SOURCE_DOCUMENT.format(
                series_id=series_id, origin=origin.casefold()
            ),
        )

    @staticmethod
    def graph_entity_id(series_id: UUID, kind: GraphEntityKind, normalized_key: str) -> UUID:
        if not isinstance(series_id, UUID) or not isinstance(kind, GraphEntityKind):
            raise ValueError(GraphErrorMessages.IDENTIFIER_FIELDS_INVALID)
        key = normalize_graph_identity(normalized_key)
        return uuid5(
            IdentifierTemplates.NAMESPACE,
            IdentifierTemplates.GRAPH_ENTITY.format(
                series_id=series_id,
                kind=kind.value,
                normalized_key=key,
            ),
        )

    @staticmethod
    def graph_entity_alias_id(entity_id: UUID, alias: str) -> UUID:
        if not isinstance(entity_id, UUID):
            raise ValueError(GraphErrorMessages.IDENTIFIER_FIELDS_INVALID)
        normalized_alias = normalize_graph_identity(alias)
        return uuid5(
            IdentifierTemplates.NAMESPACE,
            IdentifierTemplates.GRAPH_ENTITY_ALIAS.format(
                entity_id=entity_id,
                normalized_alias=normalized_alias,
            ),
        )

    @staticmethod
    def graph_claim_id(
        revision: str,
        series_id: UUID,
        subject_id: UUID,
        predicate: str,
        object_id: UUID,
        polarity: GraphClaimPolarity,
    ) -> UUID:
        if (
            not isinstance(series_id, UUID)
            or not isinstance(subject_id, UUID)
            or not isinstance(object_id, UUID)
        ):
            raise ValueError(GraphErrorMessages.IDENTIFIER_FIELDS_INVALID)
        if (
            not isinstance(polarity, GraphClaimPolarity)
            or not revision
            or revision.strip() != revision
        ):
            raise ValueError(GraphErrorMessages.IDENTIFIER_FIELDS_INVALID)
        return uuid5(
            IdentifierTemplates.NAMESPACE,
            IdentifierTemplates.GRAPH_CLAIM.format(
                revision=revision,
                series_id=series_id,
                subject=subject_id,
                predicate=normalize_graph_predicate(predicate),
                object=object_id,
                polarity=polarity.value,
            ),
        )

    @staticmethod
    def graph_evidence_id(claim_id: UUID, source_version_id: UUID, chunk_id: UUID) -> UUID:
        if not all(isinstance(value, UUID) for value in (claim_id, source_version_id, chunk_id)):
            raise ValueError(GraphErrorMessages.IDENTIFIER_FIELDS_INVALID)
        return uuid5(
            IdentifierTemplates.NAMESPACE,
            IdentifierTemplates.GRAPH_EVIDENCE.format(
                claim_id=claim_id,
                source_version_id=source_version_id,
                chunk_id=chunk_id,
            ),
        )
