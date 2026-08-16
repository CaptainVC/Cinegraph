from datetime import datetime
from typing import Protocol
from uuid import UUID

from cinegraph.application.models.netflix_history import NetflixHistoryImportRecord
from cinegraph.domain.enums.enum import NetflixHistoryImportStatus


class NetflixHistoryImportRepository(Protocol):
    def find_by_content(
        self,
        profile_id: UUID,
        content_sha256: str,
    ) -> NetflixHistoryImportRecord | None: ...

    def get(self, import_id: UUID) -> NetflixHistoryImportRecord | None: ...

    def add(self, record: NetflixHistoryImportRecord) -> None: ...

    def save(
        self,
        record: NetflixHistoryImportRecord,
        *,
        expected_status: NetflixHistoryImportStatus,
    ) -> None: ...

    def expire_sensitive_content(self, now: datetime) -> int: ...
