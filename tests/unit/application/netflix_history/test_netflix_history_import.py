from dataclasses import replace
from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest

from cinegraph.adapters.netflix_history import (
    InMemoryNetflixHistoryImportRepository,
    NetflixViewingHistoryCsvParser,
)
from cinegraph.adapters.repository.in_memory.in_memory_watch_progress_repository import (
    InMemoryWatchProgressRepository,
)
from cinegraph.application.models.netflix_history import (
    CommitNetflixHistoryImportCommand,
    NetflixHistoryUpload,
    NetflixRowApproval,
)
from cinegraph.application.service.mark_episode_watched_service import (
    MarkEpisodeWatchedService,
)
from cinegraph.application.service.netflix_history_import_service import (
    NetflixHistoryImportService,
)
from cinegraph.application.service.netflix_title_resolver import NetflixTitleResolver
from cinegraph.common.error_messages import NetflixHistoryErrorMessages
from cinegraph.config import (
    DEFAULT_NETFLIX_HISTORY_IMPORT_CONFIGURATION,
)
from cinegraph.domain.enums.enum import (
    NetflixHistoryImportStatus,
    NetflixTitleResolutionStatus,
    PrincipalKind,
    WatchEventSource,
)
from cinegraph.domain.models.catalogue.catalogue_manifest import CatalogueManifest
from cinegraph.domain.models.catalogue.episode import Episode
from cinegraph.domain.models.catalogue.season import Season
from cinegraph.domain.models.catalogue.series import Series
from cinegraph.domain.models.identity import SessionPrincipal
from cinegraph.domain.models.watch_state import ProfileWatchState
from tests.factories import (
    make_authenticated_corpus_access_scope,
    make_guest_corpus_access_scope,
)


PROFILE_ID = UUID(int=1001)
OTHER_PROFILE_ID = UUID(int=1002)
USER_ID = UUID(int=1003)
SERIES_ID = UUID(int=1004)
SEASON_ONE_ID = UUID(int=1005)
SEASON_TWO_ID = UUID(int=1006)
PILOT_ID = UUID(int=1007)
SHARED_ONE_ID = UUID(int=1008)
RETURN_ID = UUID(int=1009)
SHARED_TWO_ID = UUID(int=1010)
NOW = datetime(2026, 8, 16, 12, 0, tzinfo=UTC)


class MutableClock:
    def __init__(self) -> None:
        self.value = NOW

    def now_utc(self) -> datetime:
        return self.value


class SequenceIdentifiers:
    def __init__(self) -> None:
        self.value = 1100

    def __call__(self) -> UUID:
        self.value += 1
        return UUID(int=self.value)


def catalogue() -> CatalogueManifest:
    season_one = Season(
        series_id=SERIES_ID,
        season_id=SEASON_ONE_ID,
        season_number=1,
        episodes=(
            Episode(SERIES_ID, SEASON_ONE_ID, PILOT_ID, 1, "Pilot"),
            Episode(SERIES_ID, SEASON_ONE_ID, SHARED_ONE_ID, 2, "Shared"),
        ),
    )
    season_two = Season(
        series_id=SERIES_ID,
        season_id=SEASON_TWO_ID,
        season_number=2,
        episodes=(
            Episode(SERIES_ID, SEASON_TWO_ID, RETURN_ID, 1, "Return"),
            Episode(SERIES_ID, SEASON_TWO_ID, SHARED_TWO_ID, 2, "Shared"),
        ),
    )
    return CatalogueManifest(
        1,
        (Series(SERIES_ID, "Example Show", (season_one, season_two)),),
    )


def principal() -> SessionPrincipal:
    return SessionPrincipal(
        kind=PrincipalKind.AUTHENTICATED,
        profile_id=PROFILE_ID,
        user_id=USER_ID,
        corpus_access_scope=make_authenticated_corpus_access_scope(),
    )


def guest() -> SessionPrincipal:
    return SessionPrincipal(
        kind=PrincipalKind.GUEST,
        profile_id=PROFILE_ID,
        user_id=None,
        corpus_access_scope=make_guest_corpus_access_scope(),
    )


def upload(content: bytes | None = None) -> NetflixHistoryUpload:
    return NetflixHistoryUpload(
        filename="ViewingActivity.csv",
        content_type="text/csv; charset=utf-8",
        content=content
        or (
            b"Title,Date\r\n"
            b"Example Show: Season 1: Pilot,7/1/26\r\n"
            b"Example Show: Shared,7/2/26\r\n"
            b"Unknown Movie,7/3/26\r\n"
        ),
    )


def make_service():
    clock = MutableClock()
    imports = InMemoryNetflixHistoryImportRepository()
    watch_repository = InMemoryWatchProgressRepository(
        (ProfileWatchState(PROFILE_ID, "Viewer"),)
    )
    service = NetflixHistoryImportService(
        NetflixViewingHistoryCsvParser(),
        NetflixTitleResolver(catalogue()),
        imports,
        MarkEpisodeWatchedService(watch_repository, clock),
        clock,
        identifier_factory=SequenceIdentifiers(),
    )
    return service, imports, watch_repository, clock


def test_prepare_requires_review_and_commit_imports_only_explicit_approvals() -> None:
    service, imports, watch_repository, _ = make_service()

    review = service.prepare(principal(), upload())

    assert review.status is NetflixHistoryImportStatus.PENDING_REVIEW
    assert [item.status for item in review.resolutions] == [
        NetflixTitleResolutionStatus.MATCHED,
        NetflixTitleResolutionStatus.AMBIGUOUS,
        NetflixTitleResolutionStatus.UNMATCHED,
    ]
    assert watch_repository.watch_events == ()
    matched, ambiguous, _ = review.resolutions
    result = service.commit(
        principal(),
        CommitNetflixHistoryImportCommand(
            profile_id=PROFILE_ID,
            import_id=review.import_id,
            approvals=(
                NetflixRowApproval(
                    matched.row.row_id,
                    matched.candidates[0].episode.episode_id,
                ),
                NetflixRowApproval(
                    ambiguous.row.row_id,
                    SHARED_TWO_ID,
                ),
            ),
        ),
    )

    assert result.status is NetflixHistoryImportStatus.COMMITTED
    assert result.imported_event_count == 2
    assert set(result.approved_episode_ids) == {PILOT_ID, SHARED_TWO_ID}
    assert all(
        event.source is WatchEventSource.NETFLIX_CSV
        for event in watch_repository.watch_events
    )
    stored = imports.get(review.import_id)
    assert stored is not None
    assert stored.resolutions == ()


def test_reimport_and_recommit_are_idempotent_without_duplicate_events() -> None:
    service, _, watch_repository, _ = make_service()
    review = service.prepare(principal(), upload())
    matched = review.resolutions[0]
    command = CommitNetflixHistoryImportCommand(
        profile_id=PROFILE_ID,
        import_id=review.import_id,
        approvals=(
            NetflixRowApproval(
                matched.row.row_id,
                matched.candidates[0].episode.episode_id,
            ),
        ),
    )
    first = service.commit(principal(), command)

    repeated_review = service.prepare(principal(), upload())
    repeated_commit = service.commit(principal(), command)

    assert repeated_review.import_id == review.import_id
    assert repeated_review.status is NetflixHistoryImportStatus.COMMITTED
    assert repeated_review.resolutions == ()
    assert not first.idempotent_replay
    assert repeated_commit.idempotent_replay
    assert len(watch_repository.watch_events) == 1


def test_unmatched_or_invented_approval_fails_before_watch_state_changes() -> None:
    service, _, watch_repository, _ = make_service()
    review = service.prepare(principal(), upload())
    unmatched = review.resolutions[-1]

    with pytest.raises(
        ValueError,
        match=NetflixHistoryErrorMessages.APPROVAL_INVALID,
    ):
        service.commit(
            principal(),
            CommitNetflixHistoryImportCommand(
                PROFILE_ID,
                review.import_id,
                (NetflixRowApproval(unmatched.row.row_id, PILOT_ID),),
            ),
        )

    assert watch_repository.watch_events == ()


def test_guest_cross_profile_and_expired_review_fail_closed() -> None:
    service, imports, _, clock = make_service()
    with pytest.raises(
        PermissionError,
        match=NetflixHistoryErrorMessages.AUTHENTICATED_PRINCIPAL_REQUIRED,
    ):
        service.prepare(guest(), upload())

    review = service.prepare(principal(), upload())
    clock.value = NOW + timedelta(days=8)
    assert service.expire_sensitive_content() == 1
    stored = imports.get(review.import_id)
    assert stored is not None
    assert stored.status is NetflixHistoryImportStatus.EXPIRED
    assert stored.resolutions == ()

    restarted = service.prepare(principal(), upload())
    assert restarted.import_id == review.import_id
    assert restarted.status is NetflixHistoryImportStatus.PENDING_REVIEW
    assert restarted.resolutions

    with pytest.raises(
        PermissionError,
        match=NetflixHistoryErrorMessages.PRINCIPAL_MUST_OWN_PROFILE,
    ):
        service.commit(
            principal(),
            CommitNetflixHistoryImportCommand(
                OTHER_PROFILE_ID,
                review.import_id,
                (),
            ),
        )


def test_csv_parser_accepts_utf8_bom_and_rejects_untrusted_shapes() -> None:
    parser = NetflixViewingHistoryCsvParser()
    parsed = parser.parse(upload(b"\xef\xbb\xbfTitle,Date\r\nExample Show: Pilot,2026-07-01\r\n"))
    assert parsed.rows[0].title == "Example Show: Pilot"

    invalid_uploads = (
        replace(upload(), filename="../ViewingActivity.csv"),
        replace(upload(), content_type="application/json"),
        replace(upload(), content=b"Name,Date\r\nPilot,7/1/26\r\n"),
        replace(upload(), content=b"Title,Date\r\n=HYPERLINK(1),7/1/26\r\n"),
        replace(upload(), content=b"Title,Date\r\nPilot,not-a-date\r\n"),
        replace(upload(), content=b"\xff\xfe"),
    )
    for invalid in invalid_uploads:
        with pytest.raises(ValueError):
            parser.parse(invalid)

    small_configuration = replace(
        DEFAULT_NETFLIX_HISTORY_IMPORT_CONFIGURATION,
        maximum_upload_bytes=10,
    )
    with pytest.raises(
        ValueError,
        match=NetflixHistoryErrorMessages.FILE_SIZE_INVALID,
    ):
        NetflixViewingHistoryCsvParser(small_configuration).parse(upload())
