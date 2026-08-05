


from dataclasses import replace
from uuid import UUID

from cinegraph.common.error_messages import SourceErrorMessages
from cinegraph.domain.enums.enum import SourceVersionStatus
from cinegraph.domain.models.source.review_status import is_source_version_approved
from cinegraph.domain.models.source.source_document import SourceDocument
from cinegraph.domain.models.source.source_version import SourceVersion
from cinegraph.domain.models.transcript.transcript_segment import TranscriptSegment
from cinegraph.domain.models.watch_state.episode_watch_state import EpisodeRef


class InMemoryTranscriptIngestionRepository:
    def __init__(self) -> None:
        self._documents: dict[UUID, SourceDocument] = {}
        self._versions: dict[UUID, SourceVersion] = {}
        self._active_versions: dict[UUID, UUID] = {}
        self._segments_by_version: dict[UUID, tuple[TranscriptSegment, ...]] = {}

    def find_active_version_by_content_hash(
            self,
            source_document_id: UUID,
            content_hash: str,
    ) -> SourceVersion | None:
        active_version = self.get_active_version(source_document_id)
        if active_version is None:
            return None

        if active_version.content_hash != content_hash:
            return None

        return active_version

    def get_active_version(
            self,
            source_document_id: UUID
        ) -> SourceVersion | None:
        source_version_id = self._active_versions.get(source_document_id)
        if source_version_id is None:
            return None
        return self._versions[source_version_id]

    def persist_new_subtitle_ingestion(
            self,
            source_document: SourceDocument,
            source_version: SourceVersion,
            previous_active_version: SourceVersion | None,
            segments: tuple[TranscriptSegment, ...],
    ) -> None:

        # 1. Try to get the stored document by its ID.
        # If it doesn't exist, store it.
        # If it does exist, check if it's the same as the one being persisted.
        stored_document = self._documents.get(source_document.source_document_id)
        if stored_document is None:
            self._documents[source_document.source_document_id] = source_document
        elif stored_document != source_document:
            raise ValueError(SourceErrorMessages.SOURCE_DOCUMENT_ID_METADATA_CONFLICT)

        # 2. Store the new version.
        # If a version with the same ID already exists, raise an error.
        current_active_version = self.get_active_version(
            source_document.source_document_id
        )
        if current_active_version != previous_active_version:
            raise RuntimeError(SourceErrorMessages.ACTIVE_SOURCE_VERSION_CONFLICT)

        # 3. Store the new version and update the active version mapping.
        # If a version with the same ID already exists, raise an error.
        if any(
            segment.source_version_id != source_version.source_version_id
            for segment in segments
        ):
            raise ValueError(
                SourceErrorMessages.TRANSCRIPT_SEGMENT_SOURCE_VERSION_MISMATCH
            )

        # 4. Mark the previous active version as retired, if it exists.
        if previous_active_version is not None:
            self._versions[previous_active_version.source_version_id] = replace(
                previous_active_version,
                status=SourceVersionStatus.RETIRED,
            )

        # 5. Add the new version and its segments to the class object.
        self._versions[source_version.source_version_id] = source_version
        self._active_versions[source_document.source_document_id] = source_version.source_version_id
        self._segments_by_version[source_version.source_version_id] = segments

    def get_active_reviewed_segments(
        self,
        episode: EpisodeRef,
    ) -> tuple[TranscriptSegment, ...]:

        # 1. Resolve every active source version.
        active_source_version_ids = self._active_versions.values()

        # 2. Keep approved segments for the requested episode.
        segments = tuple(
            segment
            for source_version_id in active_source_version_ids
            if is_source_version_approved(
                self._versions[source_version_id].review_status
            )
            for segment in self._segments_by_version[source_version_id]
            if segment.episode == episode
        )

        # 3. Return deterministic transcript order.
        return tuple(
            sorted(
                segments,
                key=lambda segment: (
                    segment.start_ms,
                    segment.end_ms,
                    str(segment.segment_id),
                ),
            )
        )

    @property
    def source_versions(self) -> tuple[SourceVersion, ...]:
        return tuple(self._versions.values())

    @property
    def segments(self) -> tuple[TranscriptSegment, ...]:
        return tuple(
            segment
            for segments in self._segments_by_version.values()
            for segment in segments
        )
