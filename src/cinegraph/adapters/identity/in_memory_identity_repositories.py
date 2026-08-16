from threading import RLock

from cinegraph.common.error_messages import AuthenticationErrorMessages
from cinegraph.domain.models.identity import SessionRecord, UserAccount


class InMemoryUserAccountRepository:
    def __init__(self) -> None:
        self._accounts_by_email: dict[str, UserAccount] = {}
        self._lock = RLock()

    def get_by_email(self, normalized_email: str) -> UserAccount | None:
        with self._lock:
            return self._accounts_by_email.get(normalized_email)

    def add(self, account: UserAccount) -> None:
        with self._lock:
            if account.email in self._accounts_by_email:
                raise ValueError(
                    AuthenticationErrorMessages.EMAIL_ALREADY_REGISTERED
                )
            self._accounts_by_email[account.email] = account


class InMemorySessionRepository:
    def __init__(self) -> None:
        self._sessions_by_digest: dict[str, SessionRecord] = {}
        self._lock = RLock()

    def get_by_token_sha256(self, token_sha256: str) -> SessionRecord | None:
        with self._lock:
            return self._sessions_by_digest.get(token_sha256)

    def save(self, session: SessionRecord) -> None:
        with self._lock:
            self._sessions_by_digest[session.token_sha256] = session

    @property
    def sessions(self) -> tuple[SessionRecord, ...]:
        with self._lock:
            return tuple(self._sessions_by_digest.values())
