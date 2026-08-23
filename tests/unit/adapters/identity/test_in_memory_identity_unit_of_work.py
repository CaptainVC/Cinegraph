from datetime import UTC, datetime
from uuid import uuid4

import pytest

from cinegraph.adapters.identity import InMemoryIdentityUnitOfWorkFactory
from cinegraph.domain.enums.enum import AccountStatus
from cinegraph.domain.models.identity import UserAccount


def _account(email: str) -> UserAccount:
    return UserAccount(
        user_id=uuid4(),
        profile_id=uuid4(),
        email=email,
        display_name="Viewer",
        password_hash="hash",
        status=AccountStatus.ACTIVE,
        created_at=datetime(2026, 8, 16, tzinfo=UTC),
    )


def test_in_memory_uow_commit_and_rollback_are_transactional() -> None:
    factory = InMemoryIdentityUnitOfWorkFactory()
    with factory() as unit_of_work:
        unit_of_work.accounts.add(_account("committed@example.com"))
        unit_of_work.commit()

    with pytest.raises(RuntimeError):
        with factory() as unit_of_work:
            unit_of_work.accounts.add(_account("rolled-back@example.com"))
            raise RuntimeError("abort")

    assert factory.get_by_email("committed@example.com") is not None
    assert factory.get_by_email("rolled-back@example.com") is None


def test_in_memory_uow_does_not_leak_changes_made_after_commit() -> None:
    factory = InMemoryIdentityUnitOfWorkFactory()
    with factory() as unit_of_work:
        unit_of_work.accounts.add(_account("committed@example.com"))
        unit_of_work.commit()
        unit_of_work.accounts.add(_account("not-committed@example.com"))

    assert factory.get_by_email("committed@example.com") is not None
    assert factory.get_by_email("not-committed@example.com") is None


def test_in_memory_uow_rejects_commit_outside_context() -> None:
    unit_of_work = InMemoryIdentityUnitOfWorkFactory()()

    with pytest.raises(RuntimeError, match="not active"):
        unit_of_work.commit()
