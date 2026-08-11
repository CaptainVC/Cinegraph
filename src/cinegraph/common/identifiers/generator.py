from uuid import UUID, uuid4, uuid5

from cinegraph.common.identifiers.templates import IdentifierTemplates
from cinegraph.domain.enums.enum import Language


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
