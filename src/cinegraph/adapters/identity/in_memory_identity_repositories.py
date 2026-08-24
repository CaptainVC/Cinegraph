from copy import deepcopy
from datetime import datetime
from threading import RLock
from uuid import UUID

from cinegraph.common.error_messages import AuthenticationErrorMessages
from cinegraph.domain.models.identity import SessionRecord, UserAccount
from cinegraph.ports.identity import DuplicateEmailPersistenceError


class _InMemoryIdentityUnitOfWork:
    def __init__(self, factory: "InMemoryIdentityUnitOfWorkFactory") -> None:
        self._factory = factory
        self._lock = factory._lock
        self._accounts_by_email: dict[str, UserAccount] = {}
        self._sessions_by_digest: dict[str, SessionRecord] = {}
        self._active = False
        self._closed = False
        self.accounts = _TransactionalUserAccountRepository(self)
        self.sessions = _TransactionalSessionRepository(self)

    def __enter__(self) -> "_InMemoryIdentityUnitOfWork":
        if self._active or self._closed:
            raise RuntimeError("Identity unit of work cannot be re-entered.")
        self._lock.acquire()
        try:
            self._accounts_by_email = deepcopy(self._factory._accounts_by_email)
            self._sessions_by_digest = deepcopy(self._factory._sessions_by_digest)
            self._active = True
        except Exception:
            self._lock.release()
            raise
        return self

    def __exit__(self, exception_type: object, exception: object, traceback: object) -> None:
        try:
            if exception_type is not None:
                self.rollback()
        finally:
            self._active = False
            self._closed = True
            self._lock.release()

    def commit(self) -> None:
        if not self._active or self._closed:
            raise RuntimeError("Identity unit of work is not active.")
        self._factory._accounts_by_email = deepcopy(self._accounts_by_email)
        self._factory._sessions_by_digest = deepcopy(self._sessions_by_digest)

    def rollback(self) -> None:
        if self._active and not self._closed:
            self._accounts_by_email = deepcopy(self._factory._accounts_by_email)
            self._sessions_by_digest = deepcopy(self._factory._sessions_by_digest)


class _TransactionalUserAccountRepository:
    def __init__(self, unit_of_work: _InMemoryIdentityUnitOfWork) -> None:
        self._unit_of_work = unit_of_work

    def get_by_email(self, normalized_email: str) -> UserAccount | None:
        return self._unit_of_work._accounts_by_email.get(normalized_email)

    def get_by_user_id(
        self, user_id: UUID, *, for_update: bool = False
    ) -> UserAccount | None:
        return next(
            (account for account in self._unit_of_work._accounts_by_email.values() if account.user_id == user_id),
            None,
        )

    def add(self, account: UserAccount) -> None:
        if account.email in self._unit_of_work._accounts_by_email:
            raise DuplicateEmailPersistenceError(
                AuthenticationErrorMessages.EMAIL_ALREADY_REGISTERED
            )
        self._unit_of_work._accounts_by_email[account.email] = account

    def save(self, account: UserAccount) -> None:
        existing = self.get_by_user_id(account.user_id)
        if existing is None or existing.profile_id != account.profile_id:
            raise KeyError("Account owner not found.")
        self._unit_of_work._accounts_by_email[existing.email] = account


class _TransactionalSessionRepository:
    def __init__(self, unit_of_work: _InMemoryIdentityUnitOfWork) -> None:
        self._unit_of_work = unit_of_work

    def get_by_token_sha256(self, token_sha256: str) -> SessionRecord | None:
        return self._unit_of_work._sessions_by_digest.get(token_sha256)

    def save(self, session: SessionRecord) -> None:
        self._unit_of_work._sessions_by_digest[session.token_sha256] = session

    def _get_by_session_id(self, session_id: UUID) -> SessionRecord | None:
        return next(
            (session for session in self._unit_of_work._sessions_by_digest.values() if session.session_id == session_id),
            None,
        )

    def list_active_for_user(
        self, user_id: UUID, profile_id: UUID, now: datetime, limit: int | None
    ) -> tuple[SessionRecord, ...]:
        sessions = sorted(
            (
                session
                for session in self._unit_of_work._sessions_by_digest.values()
                if session.principal.user_id == user_id
                and session.principal.profile_id == profile_id
                and session.revoked_at is None
                and now < session.expires_at
            ),
            key=lambda item: (item.created_at, item.session_id.hex),
            reverse=True,
        )
        return tuple(sessions if limit is None else sessions[:limit])

    def revoke_session(self, session_id: UUID, user_id: UUID, profile_id: UUID, revoked_at: datetime) -> bool:
        session = self._get_by_session_id(session_id)
        if (
            session is None
            or session.principal.user_id != user_id
            or session.principal.profile_id != profile_id
            or session.revoked_at is not None
            or revoked_at >= session.expires_at
        ):
            return False
        self.save(session.revoke(revoked_at))
        return True

    def revoke_all_for_user(self, user_id: UUID, profile_id: UUID, revoked_at: datetime) -> int:
        changed = 0
        for session in tuple(self._unit_of_work._sessions_by_digest.values()):
            if (
                session.principal.user_id == user_id
                and session.principal.profile_id == profile_id
                and session.revoked_at is None
                and revoked_at < session.expires_at
            ):
                self.save(session.revoke(revoked_at))
                changed += 1
        return changed


class InMemoryIdentityUnitOfWorkFactory:
    """Fast transactional identity store for application and adapter tests."""

    def __init__(self) -> None:
        self._accounts_by_email: dict[str, UserAccount] = {}
        self._sessions_by_digest: dict[str, SessionRecord] = {}
        self._lock = RLock()

    def __call__(self) -> _InMemoryIdentityUnitOfWork:
        return _InMemoryIdentityUnitOfWork(self)

    def get_by_email(self, normalized_email: str) -> UserAccount | None:
        with self._lock:
            return self._accounts_by_email.get(normalized_email)

    def get_by_user_id(
        self, user_id: UUID, *, for_update: bool = False
    ) -> UserAccount | None:
        with self._lock:
            return next((account for account in self._accounts_by_email.values() if account.user_id == user_id), None)

    def get_by_token_sha256(self, token_sha256: str) -> SessionRecord | None:
        with self._lock:
            return self._sessions_by_digest.get(token_sha256)

    @property
    def sessions(self) -> tuple[SessionRecord, ...]:
        with self._lock:
            return tuple(self._sessions_by_digest.values())
