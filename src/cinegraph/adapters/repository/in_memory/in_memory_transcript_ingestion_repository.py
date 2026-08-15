


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
    # Initialize in-memory source-document, version, active-version, and segment stores.
    def __init__(self) -> None:
        self._documents: dict[UUID, SourceDocument] = {}
        self._versions: dict[UUID, SourceVersion] = {}
        self._active_versions: dict[UUID, UUID] = {}
        self._segments_by_version: dict[UUID, tuple[TranscriptSegment, ...]] = {}

    # Return the active transcript version only when its content hash matches.
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

    # Return the currently active transcript version for a source document.
    def get_active_version(
            self,
            source_document_id: UUID
        ) -> SourceVersion | None:
        source_version_id = self._active_versions.get(source_document_id)
        if source_version_id is None:
            return None
        return self._versions[source_version_id]

    # Validate and persist a new subtitle version and its transcript segments.
    def persist_new_subtitle_ingestion(
            self,
            source_document: SourceDocument,
            source_version: SourceVersion,
            previous_active_version: SourceVersion | None,
            segments: tuple[TranscriptSegment, ...],
    ) -> None:

        # Preserve the stable document record and reject metadata conflicts.
        stored_document = self._documents.get(source_document.source_document_id)
        if stored_document is None:
            self._documents[source_document.source_document_id] = source_document
        elif stored_document != source_document:
            raise ValueError(SourceErrorMessages.SOURCE_DOCUMENT_ID_METADATA_CONFLICT)

        # Reject writes based on an active version that changed since the caller read it.
        current_active_version = self.get_active_version(
            source_document.source_document_id
        )
        if current_active_version != previous_active_version:
            raise RuntimeError(SourceErrorMessages.ACTIVE_SOURCE_VERSION_CONFLICT)

        # Ensure every segment belongs to the version being persisted.
        if any(
            segment.source_version_id != source_version.source_version_id
            for segment in segments
        ):
            raise ValueError(
                SourceErrorMessages.TRANSCRIPT_SEGMENT_SOURCE_VERSION_MISMATCH
            )

        # Retire the previous active version before activating the new one.
        if previous_active_version is not None:
            self._versions[previous_active_version.source_version_id] = replace(
                previous_active_version,
                status=SourceVersionStatus.RETIRED,
            )

        # Store the new version, active mapping, and segment tuple.
        self._versions[source_version.source_version_id] = source_version
        self._active_versions[source_document.source_document_id] = source_version.source_version_id
        self._segments_by_version[source_version.source_version_id] = segments

    # Return approved segments from active versions for one episode in time order.
    def get_active_reviewed_segments(
        self,
        episode: EpisodeRef,
    ) -> tuple[TranscriptSegment, ...]:

        # Inspect segments belonging only to currently active source versions.
        active_source_version_ids = self._active_versions.values()

        # Keep approved segments belonging to the requested episode.
        segments = tuple(
            segment
            for source_version_id in active_source_version_ids
            if is_source_version_approved(
                self._versions[source_version_id].review_status
            )
            for segment in self._segments_by_version[source_version_id]
            if segment.episode == episode
        )

        # Sort by cue timing and identifier for deterministic retrieval.
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
    # Expose all stored transcript source versions in insertion order.
    def source_versions(self) -> tuple[SourceVersion, ...]:
        return tuple(self._versions.values())

    @property
    # Expose all stored transcript segments across source versions.
    def segments(self) -> tuple[TranscriptSegment, ...]:
        return tuple(
            segment
            for segments in self._segments_by_version.values()
            for segment in segments
        )
