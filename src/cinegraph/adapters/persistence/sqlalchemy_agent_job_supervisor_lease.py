"""Database-backed exclusivity for the single agent-job supervisor."""

from threading import Lock

import sqlalchemy as sa
from sqlalchemy.engine import Connection, Engine
from sqlalchemy.exc import SQLAlchemyError

from cinegraph.common.error_messages import AgentJobErrorMessages
from cinegraph.config.agent_jobs import AGENT_JOB_SUPERVISOR_ADVISORY_LOCK_KEY
from cinegraph.ports.agent_jobs.agent_job_repository import AgentJobUnavailableError

_DEVELOPMENT_PROCESS_LEASE = Lock()


class SqlAlchemyAgentJobSupervisorLease:
    """Hold a PostgreSQL advisory lock for the API process lifetime.

    SQLite is development-only and receives equivalent in-process exclusion so
    unit and local runtimes still detect duplicate supervisors in one process.
    """

    def __init__(self, engine: Engine) -> None:
        self._engine = engine
        self._state_lock = Lock()
        self._connection: Connection | None = None
        self._backend_pid: int | None = None
        self._development_acquired = False

    def acquire(self) -> bool:
        with self._state_lock:
            if self._connection is not None or self._development_acquired:
                return True
            if self._engine.dialect.name != "postgresql":
                acquired = _DEVELOPMENT_PROCESS_LEASE.acquire(blocking=False)
                self._development_acquired = acquired
                return acquired
            connection: Connection | None = None
            try:
                connection = self._engine.connect()
                acquired = bool(
                    connection.scalar(
                        sa.text("SELECT pg_try_advisory_lock(:lock_key)"),
                        {"lock_key": AGENT_JOB_SUPERVISOR_ADVISORY_LOCK_KEY},
                    )
                )
                backend_pid = connection.scalar(sa.text("SELECT pg_backend_pid()"))
                connection.commit()
            except SQLAlchemyError as error:
                if connection is not None:
                    connection.close()
                raise AgentJobUnavailableError(
                    AgentJobErrorMessages.SYSTEM_UNAVAILABLE
                ) from error
            if not acquired:
                assert connection is not None
                connection.close()
                return False
            assert connection is not None
            self._connection = connection
            self._backend_pid = int(backend_pid)
            return True

    def held(self) -> bool:
        with self._state_lock:
            if self._development_acquired:
                return True
            connection = self._connection
            backend_pid = self._backend_pid
            if connection is None or backend_pid is None:
                return False
            try:
                current_pid = connection.scalar(sa.text("SELECT pg_backend_pid()"))
                connection.commit()
            except SQLAlchemyError:
                self._connection = None
                self._backend_pid = None
                try:
                    connection.close()
                except SQLAlchemyError:
                    pass
                return False
            return current_pid == backend_pid

    def release(self) -> None:
        with self._state_lock:
            connection = self._connection
            self._connection = None
            self._backend_pid = None
            development_acquired = self._development_acquired
            self._development_acquired = False
        if connection is not None:
            try:
                connection.execute(
                    sa.text("SELECT pg_advisory_unlock(:lock_key)"),
                    {"lock_key": AGENT_JOB_SUPERVISOR_ADVISORY_LOCK_KEY},
                )
            except SQLAlchemyError:
                pass
            finally:
                connection.close()
        elif development_acquired:
            _DEVELOPMENT_PROCESS_LEASE.release()
