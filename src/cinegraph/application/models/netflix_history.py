from dataclasses import dataclass, replace
from datetime import date, datetime
from uuid import UUID

from cinegraph.domain.enums.enum import (
    NetflixHistoryImportStatus,
    NetflixTitleResolutionStatus,
)
from cinegraph.domain.models.watch_state import EpisodeRef


@dataclass(frozen=True, slots=True)
class NetflixHistoryUpload:
    filename: str
    content_type: str
    content: bytes


@dataclass(frozen=True, slots=True)
class NetflixViewingHistoryRow:
    row_id: str
    row_number: int
    title: str
    viewed_on: date


@dataclass(frozen=True, slots=True)
class ParsedNetflixViewingHistory:
    content_sha256: str
    rows: tuple[NetflixViewingHistoryRow, ...]


@dataclass(frozen=True, slots=True)
class NetflixEpisodeCandidate:
    episode: EpisodeRef
    series_name: str
    season_number: int
    episode_number: int
    episode_title: str | None
    reason: str


@dataclass(frozen=True, slots=True)
class NetflixTitleResolution:
    row: NetflixViewingHistoryRow
    status: NetflixTitleResolutionStatus
    candidates: tuple[NetflixEpisodeCandidate, ...]


@dataclass(frozen=True, slots=True)
class NetflixHistoryImportRecord:
    import_id: UUID
    profile_id: UUID
    content_sha256: str
    status: NetflixHistoryImportStatus
    created_at: datetime
    expires_at: datetime
    input_row_count: int
    resolutions: tuple[NetflixTitleResolution, ...]
    approved_episode_ids: tuple[UUID, ...] = ()
    imported_event_count: int = 0
    completed_at: datetime | None = None

    def commit(
        self,
        episode_ids: tuple[UUID, ...],
        imported_event_count: int,
        completed_at: datetime,
    ) -> "NetflixHistoryImportRecord":
        return replace(
            self,
            status=NetflixHistoryImportStatus.COMMITTED,
            resolutions=(),
            approved_episode_ids=episode_ids,
            imported_event_count=imported_event_count,
            completed_at=completed_at,
        )

    def expire(self) -> "NetflixHistoryImportRecord":
        return replace(
            self,
            status=NetflixHistoryImportStatus.EXPIRED,
            resolutions=(),
        )

    def restart_review(
        self,
        created_at: datetime,
        expires_at: datetime,
        resolutions: tuple[NetflixTitleResolution, ...],
    ) -> "NetflixHistoryImportRecord":
        return replace(
            self,
            status=NetflixHistoryImportStatus.PENDING_REVIEW,
            created_at=created_at,
            expires_at=expires_at,
            input_row_count=len(resolutions),
            resolutions=resolutions,
            approved_episode_ids=(),
            imported_event_count=0,
            completed_at=None,
        )


@dataclass(frozen=True, slots=True)
class NetflixHistoryImportReview:
    import_id: UUID
    content_sha256: str
    status: NetflixHistoryImportStatus
    expires_at: datetime
    input_row_count: int
    resolutions: tuple[NetflixTitleResolution, ...]
    approved_episode_ids: tuple[UUID, ...]


@dataclass(frozen=True, slots=True)
class NetflixRowApproval:
    row_id: str
    episode_id: UUID


@dataclass(frozen=True, slots=True)
class CommitNetflixHistoryImportCommand:
    profile_id: UUID
    import_id: UUID
    approvals: tuple[NetflixRowApproval, ...]


@dataclass(frozen=True, slots=True)
class NetflixHistoryImportResult:
    import_id: UUID
    status: NetflixHistoryImportStatus
    approved_episode_ids: tuple[UUID, ...]
    imported_event_count: int
    idempotent_replay: bool
