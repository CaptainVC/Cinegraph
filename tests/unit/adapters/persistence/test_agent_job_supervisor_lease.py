from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine
from sqlalchemy.exc import SQLAlchemyError

from cinegraph.adapters.persistence.sqlalchemy_agent_job_supervisor_lease import (
    SqlAlchemyAgentJobSupervisorLease,
)
from cinegraph.ports.agent_jobs.agent_job_repository import AgentJobUnavailableError


def test_development_supervisor_lease_is_exclusive_and_idempotent(tmp_path: Path) -> None:
    first_engine = create_engine(f"sqlite:///{tmp_path / 'first.db'}")
    second_engine = create_engine(f"sqlite:///{tmp_path / 'second.db'}")
    first = SqlAlchemyAgentJobSupervisorLease(first_engine)
    second = SqlAlchemyAgentJobSupervisorLease(second_engine)
    try:
        assert first.acquire()
        assert first.acquire()
        assert first.held()
        assert second.acquire() is False
        assert second.held() is False

        first.release()
        first.release()
        assert second.acquire()
        assert second.held()
    finally:
        first.release()
        second.release()
        first_engine.dispose()
        second_engine.dispose()


def test_lost_postgres_session_is_invalidated_before_reacquire() -> None:
    class FakeConnection:
        def __init__(self, backend_pid: int, *, lose_after_acquire: bool) -> None:
            self.backend_pid = backend_pid
            self.lose_after_acquire = lose_after_acquire
            self.backend_reads = 0
            self.closed = False

        def scalar(self, statement, parameters=None):
            del parameters
            sql = str(statement)
            if "pg_try_advisory_lock" in sql:
                return True
            if "pg_backend_pid" in sql:
                self.backend_reads += 1
                if self.lose_after_acquire and self.backend_reads > 1:
                    raise SQLAlchemyError("injected lost advisory session")
                return self.backend_pid
            raise AssertionError(f"unexpected SQL: {sql}")

        def commit(self) -> None:
            return None

        def execute(self, statement, parameters=None) -> None:
            del statement, parameters

        def close(self) -> None:
            self.closed = True

    class FakeEngine:
        dialect = SimpleNamespace(name="postgresql")

        def __init__(self) -> None:
            self.connections = [
                FakeConnection(101, lose_after_acquire=True),
                FakeConnection(202, lose_after_acquire=False),
            ]
            self.connect_calls = 0

        def connect(self):
            connection = self.connections[self.connect_calls]
            self.connect_calls += 1
            return connection

    engine = FakeEngine()
    lease = SqlAlchemyAgentJobSupervisorLease(engine)

    assert lease.acquire()
    assert lease.held() is False
    assert engine.connections[0].closed
    assert lease.acquire()
    assert engine.connect_calls == 2
    assert lease.held()
    lease.release()


def test_postgres_lease_acquire_failure_is_sanitized() -> None:
    class FailingEngine:
        dialect = SimpleNamespace(name="postgresql")

        def connect(self):
            raise SQLAlchemyError("private connection failure")

    lease = SqlAlchemyAgentJobSupervisorLease(FailingEngine())

    with pytest.raises(AgentJobUnavailableError, match="system is unavailable"):
        lease.acquire()


def test_postgres_lease_contention_closes_unused_connection() -> None:
    class ContendedConnection:
        def __init__(self) -> None:
            self.closed = False

        def scalar(self, statement, parameters=None):
            del parameters
            return 303 if "pg_backend_pid" in str(statement) else False

        def commit(self) -> None:
            return None

        def close(self) -> None:
            self.closed = True

    connection = ContendedConnection()
    engine = SimpleNamespace(
        dialect=SimpleNamespace(name="postgresql"),
        connect=lambda: connection,
    )
    lease = SqlAlchemyAgentJobSupervisorLease(engine)

    assert lease.acquire() is False
    assert connection.closed


def test_postgres_lease_release_is_best_effort() -> None:
    class ReleaseFailureConnection:
        def __init__(self) -> None:
            self.closed = False

        def scalar(self, statement, parameters=None):
            del parameters
            return 404 if "pg_backend_pid" in str(statement) else True

        def commit(self) -> None:
            return None

        def execute(self, statement, parameters=None) -> None:
            del statement, parameters
            raise SQLAlchemyError("injected unlock failure")

        def close(self) -> None:
            self.closed = True

    connection = ReleaseFailureConnection()
    engine = SimpleNamespace(
        dialect=SimpleNamespace(name="postgresql"),
        connect=lambda: connection,
    )
    lease = SqlAlchemyAgentJobSupervisorLease(engine)
    assert lease.acquire()

    lease.release()

    assert connection.closed
