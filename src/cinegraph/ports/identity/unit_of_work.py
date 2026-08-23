from typing import Protocol

from cinegraph.ports.identity.session_repository import SessionRepository
from cinegraph.ports.identity.user_account_repository import UserAccountRepository


class DuplicateEmailPersistenceError(ValueError):
    """Raised by a persistence adapter when the normalized email is not unique."""


class IdentityUnitOfWork(Protocol):
    """One identity command's repository scope and transaction boundary."""

    accounts: UserAccountRepository
    sessions: SessionRepository

    def __enter__(self) -> "IdentityUnitOfWork": ...

    def __exit__(self, exception_type: object, exception: object, traceback: object) -> None: ...

    def commit(self) -> None: ...

    def rollback(self) -> None: ...


class IdentityUnitOfWorkFactory(Protocol):
    def __call__(self) -> IdentityUnitOfWork: ...
