"""Submit, execute and query asynchronous conversational series jobs."""

import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol
from uuid import UUID

from cinegraph.application.models.agent_job import (
    AgentJob,
    AgentJobEvent,
)
from cinegraph.application.models.audit import (
    RuntimeTelemetryEvent,
    TelemetryOutcome,
    TelemetryStage,
)
from cinegraph.application.models.conversation import ConversationalSeriesChatQuery
from cinegraph.application.models.model_usage import (
    ModelUsageLedger,
    runtime_usage_observer_scope,
)
from cinegraph.application.models.series_agent_result import SeriesAgentResult
from cinegraph.application.service.agent_runtime_resilience import (
    AgentRuntimeBudgetExceeded,
    AgentRuntimeFailure,
    RuntimeFailureCode,
    classify_runtime_failure,
)
from cinegraph.common.error_messages import AgentJobErrorMessages
from cinegraph.common.identifiers.agent_jobs import (
    canonical_request_fingerprint,
    stable_agent_job_id,
)
from cinegraph.config import DEFAULT_AGENT_JOB_CONFIGURATION, AgentJobConfiguration
from cinegraph.domain.enums.enum import SpoilerMode
from cinegraph.domain.models.access import CorpusAccessScope
from cinegraph.domain.models.watch_state import EpisodeRef, ProfileWatchState
from cinegraph.domain.policy.watch_state_builder import build_bounded_watch_state
from cinegraph.ports.agent_jobs.agent_job_repository import AgentJobRepository
from cinegraph.ports.agent_jobs.dispatcher import AgentJobDispatcher
from cinegraph.ports.date_time.clock import Clock
from cinegraph.ports.observability import FailureIsolatingTelemetrySink, RuntimeTelemetrySink

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
    request_id: str | None = None
    spoiler_mode: SpoilerMode = SpoilerMode.RELAXED
    safe_through_episode_id: UUID | None = None


class ConversationalSeriesService(Protocol):
    def execute(self, query: ConversationalSeriesChatQuery) -> SeriesAgentResult: ...


def _job_watch_state(job: AgentJob) -> ProfileWatchState:
    """Rebuild the request's bounded spoiler policy without persisting raw state."""
    return build_bounded_watch_state(
        job.owner_profile_id,
        "API session",
        job.series_id,
        job.candidate_episodes,
        job.spoiler_mode,
        job.safe_through_episode_id,
    )


class AgentJobService:
    def __init__(
        self,
        repository: AgentJobRepository[AgentJob, AgentJobEvent, SeriesAgentResult],
        conversation_service: ConversationalSeriesService,
        dispatcher: AgentJobDispatcher,
        clock: Clock | None = None,
        configuration: AgentJobConfiguration = DEFAULT_AGENT_JOB_CONFIGURATION,
        telemetry_sink: RuntimeTelemetrySink | None = None,
        monotonic_clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._repository: AgentJobRepository[AgentJob, AgentJobEvent, SeriesAgentResult] = (
            repository
        )
        self._conversation_service = conversation_service
        self._dispatcher = dispatcher
        self._clock = clock or _UtcClock()
        self._configuration = configuration
        self._telemetry = FailureIsolatingTelemetrySink(telemetry_sink or _NoopTelemetrySink())
        self._monotonic = monotonic_clock

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
            command.spoiler_mode,
            command.safe_through_episode_id,
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
            request_id=command.request_id,
            spoiler_mode=command.spoiler_mode,
            safe_through_episode_id=command.safe_through_episode_id,
        )
        stored, created = self._repository.create(job)
        if created:
            self._emit(stored, TelemetryStage.QUEUED, TelemetryOutcome.SUCCESS)
            if not self._dispatcher.dispatch(lambda: self._execute_dispatched(stored.job_id)):
                terminal = self._repository.reject_with_event(
                    stored.job_id, AGENT_JOB_DISPATCH_FAILURE_CODE
                )
                self._emit_terminal(
                    stored, terminal, AGENT_JOB_DISPATCH_FAILURE_CODE, self._monotonic()
                )
        return self._repository.get(stored.job_id) or stored

    def execute(self, job_id: UUID) -> AgentJob | None:
        current = self._repository.get(job_id)
        if current is None or current.status.terminal:
            return current
        job = self._repository.claim_with_event(job_id)
        if job is None:
            return self._repository.get(job_id)
        started = self._monotonic()
        self._emit(job, TelemetryStage.RUNNING, TelemetryOutcome.SUCCESS)
        query = ConversationalSeriesChatQuery(
            thread_id=job.thread_id,
            profile_id=job.owner_profile_id,
            permission_scope_revision=job.permission_scope_revision,
            question=job.question,
            series_id=job.series_id,
            candidate_episodes=job.candidate_episodes,
            corpus_access_scope=job.corpus_access_scope,
            profile_watch_state=_job_watch_state(job),
        )
        try:
            with runtime_usage_observer_scope(lambda ledger: self._emit_usage(job, ledger)):
                result: SeriesAgentResult = self._conversation_service.execute(query)
        except AgentRuntimeBudgetExceeded:
            terminal = self._repository.fail_with_event(
                job_id, RuntimeFailureCode.BUDGET_EXCEEDED.value
            )
            self._emit_terminal(job, terminal, RuntimeFailureCode.BUDGET_EXCEEDED.value, started)
            return terminal
        except AgentRuntimeFailure as error:
            terminal = self._repository.fail_with_event(job_id, error.code.value)
            self._emit_terminal(job, terminal, error.code.value, started)
            return terminal
        except Exception as error:
            # Persist the stable public failure code. Repository availability or
            # transition errors are intentionally allowed to surface to the
            # worker supervisor/recovery policy; never claim a false terminal
            # state when the atomic write did not succeed.
            code = classify_runtime_failure(error)
            failure = (code or RuntimeFailureCode.EXECUTION_FAILED).value
            terminal = self._repository.fail_with_event(job_id, failure)
            self._emit_terminal(job, terminal, failure, started)
            return terminal
        terminal = self._repository.complete_with_event(job_id, result)
        self._emit_terminal(job, terminal, None, started)
        return terminal


    def _emit(
        self,
        job: AgentJob,
        stage: TelemetryStage,
        outcome: TelemetryOutcome,
        *,
        duration_ms: float | None = None,
        failure_code: str | None = None,
        model_role: str | None = None,
        model_calls: int = 0,
        tool_calls: int = 0,
        input_tokens: int = 0,
        output_tokens: int = 0,
        estimated_cost_micros: int | None = None,
        citation_count: int = 0,
    ) -> None:
        try:
            self._telemetry.emit(
                RuntimeTelemetryEvent(
                    occurred_at=self._clock.now_utc(),
                    stage=stage,
                    outcome=outcome,
                    correlation_id=job.job_id,
                    job_id=job.job_id,
                    request_id=job.request_id,
                    duration_ms=duration_ms,
                    failure_code=failure_code,
                    model_role=model_role,
                    model_calls=model_calls,
                    tool_calls=tool_calls,
                    input_tokens=input_tokens,
                    output_tokens=output_tokens,
                    estimated_cost_micros=estimated_cost_micros,
                    citation_count=citation_count,
                )
            )
        except Exception:
            return

    def _emit_terminal(
        self,
        job: AgentJob,
        terminal: AgentJob | None,
        failure: str | None,
        started: float,
    ) -> None:
        status = terminal.status.value if terminal is not None else "failed"
        outcome = (
            TelemetryOutcome.SUCCESS
            if status in {"succeeded", "safe_refusal"}
            else TelemetryOutcome.FAILURE
        )
        result = terminal.result if terminal is not None else None
        self._emit(
            job,
            TelemetryStage.TERMINAL,
            outcome,
            duration_ms=max(0.0, (self._monotonic() - started) * 1000.0),
            failure_code=failure,
            citation_count=len(result.citations) if result is not None else 0,
            tool_calls=len(result.used_tools) if result is not None else 0,
        )

    def _emit_usage(self, job: AgentJob, ledger: ModelUsageLedger) -> None:
        self._emit(
            job,
            TelemetryStage.MODEL,
            TelemetryOutcome.SUCCESS,
            model_role="aggregate",
            model_calls=len(ledger.entries),
            input_tokens=ledger.input_tokens,
            output_tokens=ledger.output_tokens,
            estimated_cost_micros=ledger.cost_micros,
        )

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


class _NoopTelemetrySink:
    def emit(self, event: RuntimeTelemetryEvent) -> None:
        del event


class AgentJobServiceProtocol(Protocol):
    def submit(self, command: SubmitAgentJobCommand) -> AgentJob: ...
    def execute(self, job_id: UUID) -> AgentJob | None: ...
    def get(self, job_id: UUID, owner_profile_id: UUID) -> AgentJob | None: ...
    def events_after(
        self, job_id: UUID, owner_profile_id: UUID, sequence: int = 0
    ) -> tuple[AgentJobEvent, ...]: ...
    def close(self) -> None: ...
