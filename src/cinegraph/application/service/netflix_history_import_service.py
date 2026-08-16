from collections.abc import Callable
from uuid import UUID

from cinegraph.application.models.mark_episode_watched import (
    MarkEpisodeWatchedCommand,
)
from cinegraph.application.models.netflix_history import (
    CommitNetflixHistoryImportCommand,
    NetflixHistoryImportRecord,
    NetflixHistoryImportResult,
    NetflixHistoryImportReview,
    NetflixHistoryUpload,
)
from cinegraph.application.service.mark_episode_watched_service import (
    MarkEpisodeWatchedService,
)
from cinegraph.application.service.netflix_title_resolver import NetflixTitleResolver
from cinegraph.common.error_messages import NetflixHistoryErrorMessages
from cinegraph.common.identifiers import IdentifierGenerator
from cinegraph.config import (
    DEFAULT_NETFLIX_HISTORY_IMPORT_CONFIGURATION,
    NetflixHistoryImportConfiguration,
)
from cinegraph.domain.enums.enum import (
    NetflixHistoryImportStatus,
    PrincipalKind,
    WatchEventSource,
)
from cinegraph.domain.models.identity import SessionPrincipal
from cinegraph.ports.date_time.clock import Clock
from cinegraph.ports.netflix_history import (
    NetflixHistoryImportRepository,
    NetflixViewingHistoryParser,
)


class NetflixHistoryImportService:
    def __init__(
        self,
        parser: NetflixViewingHistoryParser,
        resolver: NetflixTitleResolver,
        repository: NetflixHistoryImportRepository,
        watch_service: MarkEpisodeWatchedService,
        clock: Clock,
        configuration: NetflixHistoryImportConfiguration = (
            DEFAULT_NETFLIX_HISTORY_IMPORT_CONFIGURATION
        ),
        identifier_factory: Callable[[], UUID] = IdentifierGenerator.new_id,
    ) -> None:
        self._parser = parser
        self._resolver = resolver
        self._repository = repository
        self._watch_service = watch_service
        self._clock = clock
        self._configuration = configuration
        self._identifier_factory = identifier_factory

    def prepare(
        self,
        principal: SessionPrincipal,
        upload: NetflixHistoryUpload,
    ) -> NetflixHistoryImportReview:
        self._require_authenticated(principal, principal.profile_id)
        parsed = self._parser.parse(upload)
        existing = self._repository.find_by_content(
            principal.profile_id,
            parsed.content_sha256,
        )
        if existing is not None:
            now = self._clock.now_utc()
            if (
                existing.status is NetflixHistoryImportStatus.PENDING_REVIEW
                and now > existing.expires_at
            ):
                expired = existing.expire()
                self._repository.save(
                    expired,
                    expected_status=NetflixHistoryImportStatus.PENDING_REVIEW,
                )
                existing = expired
            if existing.status is NetflixHistoryImportStatus.EXPIRED:
                restarted = existing.restart_review(
                    now,
                    now + self._configuration.pending_review_retention,
                    tuple(self._resolver.resolve(row) for row in parsed.rows),
                )
                self._repository.save(
                    restarted,
                    expected_status=NetflixHistoryImportStatus.EXPIRED,
                )
                return self._review(restarted)
            return self._review(existing)
        now = self._clock.now_utc()
        record = NetflixHistoryImportRecord(
            import_id=self._identifier_factory(),
            profile_id=principal.profile_id,
            content_sha256=parsed.content_sha256,
            status=NetflixHistoryImportStatus.PENDING_REVIEW,
            created_at=now,
            expires_at=now + self._configuration.pending_review_retention,
            input_row_count=len(parsed.rows),
            resolutions=tuple(self._resolver.resolve(row) for row in parsed.rows),
        )
        self._repository.add(record)
        return self._review(record)

    def commit(
        self,
        principal: SessionPrincipal,
        command: CommitNetflixHistoryImportCommand,
    ) -> NetflixHistoryImportResult:
        self._require_authenticated(principal, command.profile_id)
        record = self._repository.get(command.import_id)
        if record is None:
            raise ValueError(NetflixHistoryErrorMessages.IMPORT_NOT_FOUND)
        if record.profile_id != command.profile_id:
            raise PermissionError(
                NetflixHistoryErrorMessages.PRINCIPAL_MUST_OWN_PROFILE
            )
        if record.status is NetflixHistoryImportStatus.COMMITTED:
            return self._result(record, idempotent_replay=True)
        if record.status is NetflixHistoryImportStatus.EXPIRED:
            raise ValueError(NetflixHistoryErrorMessages.IMPORT_EXPIRED)
        now = self._clock.now_utc()
        if now > record.expires_at:
            self._repository.save(
                record.expire(),
                expected_status=NetflixHistoryImportStatus.PENDING_REVIEW,
            )
            raise ValueError(NetflixHistoryErrorMessages.IMPORT_EXPIRED)
        row_ids = tuple(approval.row_id for approval in command.approvals)
        if len(set(row_ids)) != len(row_ids):
            raise ValueError(
                NetflixHistoryErrorMessages.APPROVAL_ROWS_MUST_BE_UNIQUE
            )
        resolutions = {
            resolution.row.row_id: resolution for resolution in record.resolutions
        }
        approved_episodes = []
        for approval in command.approvals:
            resolution = resolutions.get(approval.row_id)
            candidates = resolution.candidates if resolution is not None else ()
            candidate = next(
                (
                    item
                    for item in candidates
                    if item.episode.episode_id == approval.episode_id
                ),
                None,
            )
            if candidate is None:
                raise ValueError(NetflixHistoryErrorMessages.APPROVAL_INVALID)
            approved_episodes.append(candidate.episode)
        unique_episodes = {
            episode.episode_id: episode for episode in approved_episodes
        }
        imported_event_count = 0
        for episode_id in sorted(unique_episodes):
            outcome = self._watch_service.execute(
                MarkEpisodeWatchedCommand(
                    profile_id=command.profile_id,
                    episode=unique_episodes[episode_id],
                    source=WatchEventSource.NETFLIX_CSV,
                )
            )
            imported_event_count += outcome.watch_event is not None
        committed = record.commit(
            tuple(sorted(unique_episodes)),
            imported_event_count,
            now,
        )
        self._repository.save(
            committed,
            expected_status=NetflixHistoryImportStatus.PENDING_REVIEW,
        )
        return self._result(committed, idempotent_replay=False)

    def expire_sensitive_content(self) -> int:
        return self._repository.expire_sensitive_content(self._clock.now_utc())

    @staticmethod
    def _require_authenticated(
        principal: SessionPrincipal,
        profile_id: UUID,
    ) -> None:
        if principal.kind is not PrincipalKind.AUTHENTICATED:
            raise PermissionError(
                NetflixHistoryErrorMessages.AUTHENTICATED_PRINCIPAL_REQUIRED
            )
        if principal.profile_id != profile_id:
            raise PermissionError(
                NetflixHistoryErrorMessages.PRINCIPAL_MUST_OWN_PROFILE
            )

    @staticmethod
    def _review(record: NetflixHistoryImportRecord) -> NetflixHistoryImportReview:
        return NetflixHistoryImportReview(
            import_id=record.import_id,
            content_sha256=record.content_sha256,
            status=record.status,
            expires_at=record.expires_at,
            input_row_count=record.input_row_count,
            resolutions=record.resolutions,
            approved_episode_ids=record.approved_episode_ids,
        )

    @staticmethod
    def _result(
        record: NetflixHistoryImportRecord,
        *,
        idempotent_replay: bool,
    ) -> NetflixHistoryImportResult:
        return NetflixHistoryImportResult(
            import_id=record.import_id,
            status=record.status,
            approved_episode_ids=record.approved_episode_ids,
            imported_event_count=record.imported_event_count,
            idempotent_replay=idempotent_replay,
        )
