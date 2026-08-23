from datetime import UTC, datetime
from uuid import UUID

import pytest

from cinegraph.adapters.repository.in_memory.in_memory_series_metadata_ingestion_repository import (
    InMemorySeriesMetadataIngestionRepository,
)
from cinegraph.application.service.ingest_series_metadata_service import (
    IngestSeriesMetadataService,
)
from cinegraph.domain.enums.enum import RightsStatus, SourceKind, SourceReviewStatus
from cinegraph.domain.models.series_metadata import EpisodeCastMetadata
from cinegraph.domain.models.source.source_document import SourceDocument
from cinegraph.domain.models.watch_state import EpisodePosition, EpisodeRef
from cinegraph.ingestion.series_metadata.ingest_series_metadata import (
    IngestSeriesMetadataCommand,
)
from cinegraph.ports.dto.fetched_series_metadata import FetchedSeriesMetadata


class _Provider:
    def __init__(self, episode):
        self.episode = episode
        self.calls = 0

    def fetch(self, **kwargs):
        self.calls += 1
        return FetchedSeriesMetadata(
            "TVmaze",
            80,
            "Modern Family",
            "https://www.tvmaze.com/shows/80/modern-family",
            None,
            (),
            (self.episode,),
            datetime(2026, 1, 1, tzinfo=UTC),
            "TVmaze, licensed under CC BY-SA",
            "Creative Commons Attribution-ShareAlike 4.0 International",
            "https://creativecommons.org/licenses/by-sa/4.0/",
        )


def test_identical_active_metadata_is_idempotent_and_pending_is_not_visible():
    series_id = UUID("00000000-0000-0000-0000-000000000001")
    episode = EpisodeRef(
        series_id,
        UUID("00000000-0000-0000-0000-000000000002"),
        UUID("00000000-0000-0000-0000-000000000003"),
        EpisodePosition(1, 1),
    )
    item = EpisodeCastMetadata(
        episode, 10, "Pilot", "https://www.tvmaze.com/episodes/10/pilot", ()
    )
    provider = _Provider(item)
    repository = InMemorySeriesMetadataIngestionRepository()
    command = IngestSeriesMetadataCommand(
        SourceDocument(
            UUID("00000000-0000-0000-0000-000000000010"),
            "TVmaze metadata",
            SourceKind.METADATA,
            "tvmaze",
        ),
        series_id,
        80,
        "Modern Family",
        (episode,),
        RightsStatus.ALLOWED,
    )
    service = IngestSeriesMetadataService(provider, repository)
    first = service.execute(command)
    second = service.execute(command)
    assert not first.was_already_ingested
    assert second.was_already_ingested
    assert (
        repository.get_active_reviewed_series_metadata(
            command.source_document.source_document_id
        )
        is None
    )


def test_changed_payload_retires_parent_and_approved_active_snapshot_is_visible():
    series_id = UUID("00000000-0000-0000-0000-000000000001")
    episode = EpisodeRef(
        series_id,
        UUID("00000000-0000-0000-0000-000000000002"),
        UUID("00000000-0000-0000-0000-000000000003"),
        EpisodePosition(1, 1),
    )
    first_item = EpisodeCastMetadata(
        episode, 10, "Pilot", "https://www.tvmaze.com/episodes/10/pilot", ()
    )
    second_item = EpisodeCastMetadata(
        episode, 11, "Pilot", "https://www.tvmaze.com/episodes/11/pilot", ()
    )
    provider = _Provider(first_item)
    repository = InMemorySeriesMetadataIngestionRepository()
    command = IngestSeriesMetadataCommand(
        SourceDocument(
            UUID("00000000-0000-0000-0000-000000000010"),
            "TVmaze metadata",
            SourceKind.METADATA,
            "tvmaze",
        ),
        series_id,
        80,
        "Modern Family",
        (episode,),
    )
    service = IngestSeriesMetadataService(provider, repository)
    first = service.execute(command)
    provider.episode = second_item
    second = service.execute(command)
    assert (
        second.source_version.parent_source_version_id
        == first.source_version.source_version_id
    )
    assert (
        repository.get_source_version(
            first.source_version.source_version_id
        ).status.value
        == "retired"
    )
    repository.update_source_version_review_status(
        second.source_version.source_version_id,
        SourceReviewStatus.REVIEWED,
        "reviewer",
        datetime(2026, 1, 2, tzinfo=UTC),
    )
    assert (
        repository.get_active_reviewed_series_metadata(
            command.source_document.source_document_id
        )
        is not None
    )

    repository.update_source_version_review_status(
        second.source_version.source_version_id,
        SourceReviewStatus.REJECTED,
        "reviewer",
        datetime(2026, 1, 3, tzinfo=UTC),
    )
    assert (
        repository.get_active_reviewed_series_metadata(
            command.source_document.source_document_id
        )
        is None
    )

    stale = first.source_version
    with pytest.raises(RuntimeError):
        repository.persist_new_series_metadata_ingestion(
            command.source_document, second.source_version, stale, second.snapshot
        )
