from datetime import datetime
from threading import RLock
from uuid import UUID

from cinegraph.application.models.netflix_history import NetflixHistoryImportRecord
from cinegraph.common.error_messages import NetflixHistoryErrorMessages
from cinegraph.domain.enums.enum import NetflixHistoryImportStatus


class InMemoryNetflixHistoryImportRepository:
    def __init__(self) -> None:
        self._records: dict[UUID, NetflixHistoryImportRecord] = {}
        self._content_index: dict[tuple[UUID, str], UUID] = {}
        self._lock = RLock()

    def find_by_content(
        self,
        profile_id: UUID,
        content_sha256: str,
    ) -> NetflixHistoryImportRecord | None:
        with self._lock:
            import_id = self._content_index.get((profile_id, content_sha256))
            return self._records.get(import_id) if import_id is not None else None

    def get(self, import_id: UUID) -> NetflixHistoryImportRecord | None:
        with self._lock:
            return self._records.get(import_id)

    def add(self, record: NetflixHistoryImportRecord) -> None:
        with self._lock:
            content_key = (record.profile_id, record.content_sha256)
            if record.import_id in self._records or content_key in self._content_index:
                raise ValueError(NetflixHistoryErrorMessages.REPOSITORY_CONFLICT)
            self._records[record.import_id] = record
            self._content_index[content_key] = record.import_id

    def save(
        self,
        record: NetflixHistoryImportRecord,
        *,
        expected_status: NetflixHistoryImportStatus,
    ) -> None:
        with self._lock:
            current = self._records.get(record.import_id)
            if current is None or current.status is not expected_status:
                raise ValueError(NetflixHistoryErrorMessages.REPOSITORY_CONFLICT)
            self._records[record.import_id] = record

    def expire_sensitive_content(self, now: datetime) -> int:
        with self._lock:
            expiring = tuple(
                record
                for record in self._records.values()
                if record.status is NetflixHistoryImportStatus.PENDING_REVIEW
                and now > record.expires_at
            )
            for record in expiring:
                self._records[record.import_id] = record.expire()
            return len(expiring)
