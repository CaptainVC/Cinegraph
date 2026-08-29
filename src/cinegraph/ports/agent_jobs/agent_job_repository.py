from typing import Protocol, TypeVar
from uuid import UUID


class AgentJobIdempotencyConflictError(ValueError):
    """The same owner/key was submitted with a different request fingerprint."""


class AgentJobUnavailableError(RuntimeError):
    """The job store is unavailable."""


class AgentJobTransitionError(RuntimeError):
    """An illegal state transition was requested."""


JobT = TypeVar("JobT")
EventT = TypeVar("EventT", covariant=True)
ResultT = TypeVar("ResultT", contravariant=True)


class AgentJobRepository(Protocol[JobT, EventT, ResultT]):
    def create(self, job: JobT) -> tuple[JobT, bool]: ...
    def claim_with_event(
        self, job_id: UUID, owner_profile_id: UUID | None = None
    ) -> JobT | None: ...
    def get(self, job_id: UUID, owner_profile_id: UUID | None = None) -> JobT | None: ...
    def complete_with_event(
        self, job_id: UUID, result: ResultT, owner_profile_id: UUID | None = None
    ) -> JobT | None: ...
    def fail_with_event(
        self, job_id: UUID, error_code: str, owner_profile_id: UUID | None = None
    ) -> JobT | None: ...
    def reject_with_event(
        self, job_id: UUID, error_code: str, owner_profile_id: UUID | None = None
    ) -> JobT | None: ...
    def requeue_running_job_with_event(self, job_id: UUID) -> tuple[JobT | None, bool]: ...
    def requeue_running_with_event(self, limit: int) -> tuple[JobT, ...]: ...
    def list_queued(self, limit: int) -> tuple[JobT, ...]: ...
    def list_events_after(
        self, job_id: UUID, sequence: int = 0, owner_profile_id: UUID | None = None
    ) -> tuple[EventT, ...]: ...
