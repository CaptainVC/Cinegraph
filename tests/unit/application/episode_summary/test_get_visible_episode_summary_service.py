from datetime import UTC, datetime
from uuid import UUID

from cinegraph.adapters.repository.in_memory.in_memory_episode_summary_ingestion_repository import (
    InMemoryEpisodeSummaryIngestionRepository,
)
from cinegraph.application.models.get_visible_episode_summary import (
    GetVisibleEpisodeSummaryQuery,
)
from cinegraph.application.service.get_visible_episode_summary_service import (
    GetVisibleEpisodeSummaryService,
)
from cinegraph.config import DEFAULT_GUEST_CORPUS_ACCESS_SCOPE
from cinegraph.domain.enums.enum import (
    Language,
    RightsStatus,
    SourceAcquisitionMethod,
    SourceKind,
    SourceReviewStatus,
    SourceVersionStatus,
    SpoilerMode,
)
from cinegraph.domain.models.episode_summary.episode_summary_document import (
    EpisodeSummaryDocument,
)
from cinegraph.domain.models.source.source_document import SourceDocument
from cinegraph.domain.models.source.source_version import SourceVersion
from cinegraph.domain.models.watch_state.episode_watch_state import (
    EpisodeWatchProgress,
)
from cinegraph.domain.models.watch_state.profile_watch_state import (
    ProfileWatchState,
)
from cinegraph.domain.models.watch_state.series_watch_state import (
    SeriesWatchState,
)
from cinegraph.domain.policy.spoiler_policy import SpoilerPolicy
from tests.factories import make_authenticated_corpus_access_scope, make_episode_ref

SOURCE_DOCUMENT_ID = UUID("00000000-0000-0000-0000-000000000401")
SOURCE_VERSION_ID = UUID("00000000-0000-0000-0000-000000000501")
SUMMARY_ID = UUID("00000000-0000-0000-0000-000000000601")
PROFILE_ID = UUID("00000000-0000-0000-0000-000000000701")
TIMESTAMP = datetime(2026, 8, 5, 15, 0, tzinfo=UTC)


def reviewed_repository(*, episode=None) -> tuple[
    InMemoryEpisodeSummaryIngestionRepository,
    EpisodeSummaryDocument,
]:
    repository = InMemoryEpisodeSummaryIngestionRepository()
    episode = episode or make_episode_ref()
    source_document = SourceDocument(
        source_document_id=SOURCE_DOCUMENT_ID,
        title="Modern Family S01E01 summary",
        kind=SourceKind.EPISODE_PLOT,
        origin="wikipedia",
    )
    source_version = SourceVersion(
        source_version_id=SOURCE_VERSION_ID,
        source_document_id=SOURCE_DOCUMENT_ID,
        content_hash="a" * 64,
        rights_status=RightsStatus.ALLOWED,
        acquisition_method=SourceAcquisitionMethod.MEDIAWIKI_API,
        review_status=SourceReviewStatus.REVIEWED,
        status=SourceVersionStatus.ACTIVE,
        acquired_at=TIMESTAMP,
        reviewed_by="local-corpus-owner",
        reviewed_at=TIMESTAMP,
    )
    summary = EpisodeSummaryDocument(
        summary_id=SUMMARY_ID,
        source_version_id=SOURCE_VERSION_ID,
        episode=episode,
        text="A concise episode summary.",
        language=Language.ENGLISH,
        rights_status=RightsStatus.ALLOWED,
        canonical_url="https://en.wikipedia.org/wiki/Pilot_(Modern_Family)",
        revision_id=123,
        revision_timestamp=TIMESTAMP,
        attribution="Wikipedia contributors, CC BY-SA",
    )
    repository.persist_new_episode_summary_ingestion(
        source_document=source_document,
        source_version=source_version,
        previous_active_version=None,
        summary=summary,
    )
    return repository, summary


def service(repository: InMemoryEpisodeSummaryIngestionRepository) -> GetVisibleEpisodeSummaryService:
    return GetVisibleEpisodeSummaryService(
        reader=repository,
        spoiler_policy=SpoilerPolicy(),
    )


def test_returns_reviewed_summary_for_fully_watched_episode() -> None:
    repository, summary = reviewed_repository()
    watch_state = ProfileWatchState(
        profile_id=PROFILE_ID,
        profile_name="Maya",
        series_watch_states=(
            SeriesWatchState(
                series_id=summary.episode.series_id,
                episode_progress=(
                    EpisodeWatchProgress(
                        episode=summary.episode,
                        is_completed=True,
                    ),
                ),
            ),
        ),
    )

    result = service(repository).execute(
        GetVisibleEpisodeSummaryQuery(
            source_document_id=SOURCE_DOCUMENT_ID,
            profile_watch_state=watch_state,
            corpus_access_scope=make_authenticated_corpus_access_scope(),
        )
    )

    assert result.summary == summary
    assert result.safe_until_ms is None
    assert result.is_model_context_only is False


def test_returns_summary_as_model_context_for_partial_episode_watch() -> None:
    repository, summary = reviewed_repository()
    watch_state = ProfileWatchState(
        profile_id=PROFILE_ID,
        profile_name="Maya",
        series_watch_states=(
            SeriesWatchState(
                series_id=summary.episode.series_id,
                episode_progress=(
                    EpisodeWatchProgress(
                        episode=summary.episode,
                        safe_until_ms=32_000,
                    ),
                ),
            ),
        ),
    )

    result = service(repository).execute(
        GetVisibleEpisodeSummaryQuery(
            source_document_id=SOURCE_DOCUMENT_ID,
            profile_watch_state=watch_state,
            corpus_access_scope=make_authenticated_corpus_access_scope(),
        )
    )

    assert result.summary == summary
    assert result.safe_until_ms == 32_000
    assert result.is_model_context_only is True


def test_hides_reviewed_summary_for_unwatched_episode() -> None:
    repository, _summary = reviewed_repository()
    watch_state = ProfileWatchState(
        profile_id=PROFILE_ID,
        profile_name="Maya",
    )

    result = service(repository).execute(
        GetVisibleEpisodeSummaryQuery(
            source_document_id=SOURCE_DOCUMENT_ID,
            profile_watch_state=watch_state,
            corpus_access_scope=make_authenticated_corpus_access_scope(),
        )
    )

    assert result.summary is None
    assert result.safe_until_ms is None
    assert result.is_model_context_only is False


def test_hides_summary_until_source_version_is_reviewed() -> None:
    repository, summary = reviewed_repository()
    repository.update_source_version_review_status(
        source_version_id=summary.source_version_id,
        review_status=SourceReviewStatus.REJECTED,
        reviewed_by="local-corpus-owner",
        reviewed_at=TIMESTAMP,
    )

    result = service(repository).execute(
        GetVisibleEpisodeSummaryQuery(
            source_document_id=SOURCE_DOCUMENT_ID,
            profile_watch_state=None,
            corpus_access_scope=make_authenticated_corpus_access_scope(),
        )
    )

    assert result.summary is None
    assert result.safe_until_ms is None
    assert result.is_model_context_only is False


def test_guest_scope_hides_authenticated_only_season_even_in_relaxed_mode() -> None:
    repository, _summary = reviewed_repository(
        episode=make_episode_ref(
            season_id=UUID(int=300),
            season_number=3,
        )
    )
    relaxed_watch_state = ProfileWatchState(
        profile_id=PROFILE_ID,
        profile_name="Guest",
        spoiler_mode=SpoilerMode.RELAXED,
    )

    result = service(repository).execute(
        GetVisibleEpisodeSummaryQuery(
            source_document_id=SOURCE_DOCUMENT_ID,
            profile_watch_state=relaxed_watch_state,
            corpus_access_scope=DEFAULT_GUEST_CORPUS_ACCESS_SCOPE,
        )
    )

    assert result.summary is None
