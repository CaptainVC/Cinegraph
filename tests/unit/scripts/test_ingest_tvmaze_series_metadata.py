from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import UUID

import pytest
from scripts.ingest_tvmaze_series_metadata import _output_path, write_snapshot

from cinegraph.domain.enums.enum import (
    RightsStatus,
    SourceAcquisitionMethod,
    SourceKind,
    SourceReviewStatus,
    SourceVersionStatus,
)
from cinegraph.domain.models.series_metadata import (
    EpisodeCastMetadata,
    SeriesMetadataSnapshot,
)
from cinegraph.domain.models.source.source_document import SourceDocument
from cinegraph.domain.models.watch_state import EpisodePosition, EpisodeRef
from cinegraph.ingestion.series_metadata.ingest_series_metadata import (
    IngestSeriesMetadataResult,
)


def _result(content_hash: str = "a" * 64):
    series_id = UUID(int=1)
    source_document = SourceDocument(
        UUID(int=2), "TVmaze metadata", SourceKind.METADATA, "tvmaze"
    )
    version = SimpleNamespace(
        source_document_id=source_document.source_document_id,
        source_version_id=UUID(int=3),
        content_hash=content_hash,
        rights_status=RightsStatus.ALLOWED,
        acquisition_method=SourceAcquisitionMethod.TVMAZE_API,
        review_status=SourceReviewStatus.PENDING,
        status=SourceVersionStatus.ACTIVE,
        acquired_at=datetime(2026, 1, 1, tzinfo=UTC),
        parent_source_version_id=None,
    )
    episode = EpisodeCastMetadata(
        EpisodeRef(series_id, UUID(int=4), UUID(int=5), EpisodePosition(1, 1)),
        10,
        "Pilot",
        "https://www.tvmaze.com/episodes/10/pilot",
        (),
    )
    snapshot = SeriesMetadataSnapshot(
        series_id,
        version.source_version_id,
        "TVmaze",
        80,
        "Modern Family",
        "https://www.tvmaze.com/shows/80/modern-family",
        None,
        (),
        (episode,),
        RightsStatus.ALLOWED,
        "TVmaze, licensed under CC BY-SA",
        "Creative Commons Attribution-ShareAlike 4.0 International",
        "https://creativecommons.org/licenses/by-sa/4.0/",
    )
    return source_document, IngestSeriesMetadataResult(version, snapshot, False)


def test_output_containment_and_safe_replacement(tmp_path):
    manifest = tmp_path / "knowledge" / "catalogue.json"
    inside = manifest.parent / "metadata.json"
    outside = tmp_path / "metadata.json"
    assert _output_path(manifest, inside) == inside.resolve()
    with pytest.raises(ValueError):
        _output_path(manifest, outside)
    source_document, result = _result()
    assert write_snapshot(inside, source_document, result)
    assert not write_snapshot(inside, source_document, result)
    changed_source, changed = _result("b" * 64)
    with pytest.raises(FileExistsError):
        write_snapshot(inside, changed_source, changed)
    assert write_snapshot(inside, changed_source, changed, force=True)
