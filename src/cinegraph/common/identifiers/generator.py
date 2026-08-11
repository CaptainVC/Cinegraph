from uuid import UUID, uuid4, uuid5

from cinegraph.common.identifiers.templates import IdentifierTemplates
from cinegraph.domain.enums.enum import Language


class IdentifierGenerator:
    @staticmethod
    # Processes the supplied new id values.
    def new_id() -> UUID:
        return uuid4()

    @staticmethod
    # Processes the supplied source version id values.
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
    # Processes the supplied speaker id values.
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
    # Processes the supplied transcript segment id values.
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
    # Processes the supplied episode summary id values.
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
    # Processes the supplied episode summary document id values.
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
    # Processes the supplied episode summary source document id values.
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
