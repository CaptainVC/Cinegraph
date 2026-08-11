from typing import Protocol

from uuid import UUID

from cinegraph.domain.models.source.source_document import SourceDocument
from cinegraph.domain.models.source.source_version import SourceVersion
from cinegraph.domain.models.transcript.transcript_segment import TranscriptSegment


class TranscriptIngestionRepository(Protocol):

    # Finds and returns the matching value when available.
    def find_active_version_by_content_hash(
            self,
            source_document_id: UUID,
            content_hash: str,
    ) -> SourceVersion | None: ...

    # Gets and returns the requested value.
    def get_active_version(
            self,
            source_document_id: UUID,
    ) -> SourceVersion | None: ...

    # Persists the supplied value in the repository.
    def persist_new_subtitle_ingestion(
            self,
            source_document: SourceDocument,
            source_version: SourceVersion,
            previous_active_version: SourceVersion | None,
            segments: tuple[TranscriptSegment, ...],
    ) -> None: ...
