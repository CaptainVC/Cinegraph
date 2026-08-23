"""Submit, execute and query asynchronous conversational series jobs."""

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol
from uuid import UUID

from cinegraph.application.models.agent_job import (
    AgentJob,
    AgentJobEvent,
)
from cinegraph.application.models.conversation import ConversationalSeriesChatQuery
from cinegraph.application.models.series_agent_result import SeriesAgentResult
from cinegraph.common.error_messages import AgentJobErrorMessages
from cinegraph.common.identifiers.agent_jobs import (
    canonical_request_fingerprint,
    stable_agent_job_id,
)
from cinegraph.config import DEFAULT_AGENT_JOB_CONFIGURATION, AgentJobConfiguration
from cinegraph.domain.models.access import CorpusAccessScope
from cinegraph.domain.models.watch_state import EpisodeRef
from cinegraph.ports.agent_jobs.agent_job_repository import AgentJobRepository
from cinegraph.ports.agent_jobs.dispatcher import AgentJobDispatcher
from cinegraph.ports.date_time.clock import Clock

AGENT_JOB_FAILURE_CODE = AgentJobErrorMessages.EXECUTION_FAILED
AGENT_JOB_DISPATCH_FAILURE_CODE = AgentJobErrorMessages.DISPATCH_UNAVAILABLE


@dataclass(frozen=True, slots=True)
class SubmitAgentJobCommand:
    owner_profile_id: UUID
    thread_id: UUID
    series_id: UUID
    question: str
    permission_scope_revision: str
    corpus_access_scope: CorpusAccessScope
    candidate_episodes: tuple[EpisodeRef, ...]
    idempotency_key: str


class ConversationalSeriesService(Protocol):
    def execute(self, query: ConversationalSeriesChatQuery) -> SeriesAgentResult: ...


class AgentJobService:
    def __init__(
        self,
        repository: AgentJobRepository[AgentJob, AgentJobEvent, SeriesAgentResult],
        conversation_service: ConversationalSeriesService,
        dispatcher: AgentJobDispatcher,
        clock: Clock | None = None,
        configuration: AgentJobConfiguration = DEFAULT_AGENT_JOB_CONFIGURATION,
    ) -> None:
        self._repository: AgentJobRepository[AgentJob, AgentJobEvent, SeriesAgentResult] = (
            repository
        )
        self._conversation_service = conversation_service
        self._dispatcher = dispatcher
        self._clock = clock or _UtcClock()
        self._configuration = configuration

    def submit(self, command: SubmitAgentJobCommand) -> AgentJob:
        try:
            parsed_key = UUID(command.idempotency_key)
        except (TypeError, ValueError, AttributeError) as error:
            raise ValueError(AgentJobErrorMessages.IDEMPOTENCY_INVALID) from error
        if str(parsed_key) != command.idempotency_key:
            raise ValueError(AgentJobErrorMessages.IDEMPOTENCY_INVALID)
        if (
            not self._configuration.question_min_length
            <= len(command.question)
            <= self._configuration.question_max_length
        ):
            raise ValueError(AgentJobErrorMessages.QUESTION_BOUNDS)
        if len(command.candidate_episodes) > self._configuration.candidate_max_episodes:
            raise ValueError(AgentJobErrorMessages.CANDIDATE_LIMIT)
        if len({item.episode_id for item in command.candidate_episodes}) != len(
            command.candidate_episodes
        ):
            raise ValueError(AgentJobErrorMessages.CANDIDATES_UNIQUE)
        candidates = tuple(
            sorted(
                command.candidate_episodes,
                key=lambda item: (
                    item.position.season_number,
                    item.position.episode_number,
                    item.episode_id.hex,
                ),
            )
        )
        fingerprint = canonical_request_fingerprint(
            command.owner_profile_id,
            command.thread_id,
            command.series_id,
            command.question,
            command.permission_scope_revision,
            command.corpus_access_scope,
            candidates,
        )
        job = AgentJob(
            job_id=stable_agent_job_id(
                command.owner_profile_id, command.idempotency_key, fingerprint
            ),
            owner_profile_id=command.owner_profile_id,
            thread_id=command.thread_id,
            series_id=command.series_id,
            question=command.question,
            candidate_episodes=candidates,
            corpus_access_scope=command.corpus_access_scope,
            permission_scope_revision=command.permission_scope_revision,
            idempotency_key=command.idempotency_key,
            request_fingerprint=fingerprint,
            created_at=self._clock.now_utc(),
        )
        stored, created = self._repository.create(job)
        if created:
            if not self._dispatcher.dispatch(lambda: self._execute_dispatched(stored.job_id)):
                self._repository.reject_with_event(stored.job_id, AGENT_JOB_DISPATCH_FAILURE_CODE)
        return self._repository.get(stored.job_id) or stored

    def execute(self, job_id: UUID) -> AgentJob | None:
        current = self._repository.get(job_id)
        if current is None or current.status.terminal:
            return current
        job = self._repository.claim_with_event(job_id)
        if job is None:
            return self._repository.get(job_id)
        query = ConversationalSeriesChatQuery(
            thread_id=job.thread_id,
            profile_id=job.owner_profile_id,
            permission_scope_revision=job.permission_scope_revision,
            question=job.question,
            series_id=job.series_id,
            candidate_episodes=job.candidate_episodes,
            corpus_access_scope=job.corpus_access_scope,
        )
        try:
            result: SeriesAgentResult = self._conversation_service.execute(query)
        except Exception:
            updated = self._repository.fail_with_event(job_id, AGENT_JOB_FAILURE_CODE)
            return updated
        return self._repository.complete_with_event(job_id, result)

    def get(self, job_id: UUID, owner_profile_id: UUID) -> AgentJob | None:
        return self._repository.get(job_id, owner_profile_id)

    def events_after(
        self, job_id: UUID, owner_profile_id: UUID, sequence: int = 0
    ) -> tuple[AgentJobEvent, ...]:
        return self._repository.list_events_after(job_id, sequence, owner_profile_id)

    def close(self) -> None:
        self._dispatcher.close()

    def _execute_dispatched(self, job_id: UUID) -> None:
        self.execute(job_id)


# Explicit aliases make the submit/execute/query boundaries discoverable while
# preserving one atomic service and one injected repository.
AgentJobSubmissionService = AgentJobService
AgentJobExecutionService = AgentJobService
AgentJobQueryService = AgentJobService


class _UtcClock:
    def now_utc(self) -> datetime:
        return datetime.now(UTC)


class AgentJobServiceProtocol(Protocol):
    def submit(self, command: SubmitAgentJobCommand) -> AgentJob: ...
    def execute(self, job_id: UUID) -> AgentJob | None: ...
    def get(self, job_id: UUID, owner_profile_id: UUID) -> AgentJob | None: ...
    def events_after(
        self, job_id: UUID, owner_profile_id: UUID, sequence: int = 0
    ) -> tuple[AgentJobEvent, ...]: ...
    def close(self) -> None: ...
