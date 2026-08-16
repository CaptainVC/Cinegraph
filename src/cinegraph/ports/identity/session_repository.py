from typing import Protocol

from cinegraph.domain.models.identity import SessionRecord


class SessionRepository(Protocol):
    def get_by_token_sha256(self, token_sha256: str) -> SessionRecord | None: ...

    def save(self, session: SessionRecord) -> None: ...
