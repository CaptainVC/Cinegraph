from datetime import datetime
from typing import Protocol
from uuid import UUID

from cinegraph.domain.models.identity import SessionRecord


class SessionRepository(Protocol):
    def get_by_token_sha256(self, token_sha256: str) -> SessionRecord | None: ...

    def save(self, session: SessionRecord) -> None: ...

    def list_active_for_user(
        self, user_id: UUID, profile_id: UUID, now: datetime, limit: int | None
    ) -> tuple[SessionRecord, ...]: ...

    def revoke_session(self, session_id: UUID, user_id: UUID, profile_id: UUID, revoked_at: datetime) -> bool: ...

    def revoke_all_for_user(self, user_id: UUID, profile_id: UUID, revoked_at: datetime) -> int: ...
