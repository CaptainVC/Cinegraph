from uuid import UUID, uuid4, uuid5

from cinegraph.common.identifiers.templates import IdentifierTemplates
from cinegraph.domain.enums.enum import Language


class IdentifierGenerator:
    @staticmethod
    def new_id() -> UUID:
        return uuid4()

    @staticmethod
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