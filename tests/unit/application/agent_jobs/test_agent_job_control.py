from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import UTC, datetime
from threading import Barrier, Event
from uuid import uuid4

import pytest

from cinegraph.adapters.repository.in_memory.in_memory_agent_job_repository import (
    InMemoryAgentJobRepository,
)
from cinegraph.application.models.agent_job import AgentJob, AgentJobEvent, AgentJobEventKind
from cinegraph.application.models.audit import TelemetryOutcome, TelemetryStage
from cinegraph.application.models.model_usage import (
    ModelUsage,
    ModelUsageLedger,
    current_runtime_usage_observer,
)
from cinegraph.application.models.series_agent_result import SeriesAgentResult
from cinegraph.application.service.agent_job_service import AgentJobService, SubmitAgentJobCommand
from cinegraph.application.service.agent_runtime_resilience import (
    AgentRuntimeBudgetExceeded,
    AgentRuntimeFailure,
    RuntimeFailureCode,
)
from cinegraph.common.error_messages import AgentJobErrorMessages
from cinegraph.common.identifiers.agent_jobs import (
    canonical_request_fingerprint,
    stable_agent_job_id,
)
from cinegraph.config import AgentJobConfiguration
from cinegraph.domain.enums.enum import CorpusAccessMode
from cinegraph.domain.models.access import CorpusAccessScope, CorpusSeasonAccess
from cinegraph.domain.models.watch_state import EpisodePosition, EpisodeRef
from cinegraph.ports.agent_jobs.agent_job_repository import (
    AgentJobIdempotencyConflictError,
    AgentJobTransitionError,
)
from cinegraph.ports.agent_jobs.dispatcher import (
    BoundedThreadPoolAgentJobDispatcher,
    InlineAgentJobDispatcher,
)


def _command(key: str | None = None) -> SubmitAgentJobCommand:
    series = uuid4()
    episode = EpisodeRef(series, uuid4(), uuid4(), EpisodePosition(1, 1))
    profile = uuid4()
    scope = CorpusAccessScope(
        CorpusAccessMode.GUEST, "scope-1", frozenset({CorpusSeasonAccess(series, 1)})
    )
    return SubmitAgentJobCommand(
        profile,
        uuid4(),
        series,
        "A bounded question",
        "scope-1",
        scope,
        (episode,),
        key or str(uuid4()),
    )


def _job(command: SubmitAgentJobCommand) -> AgentJob:
    fingerprint = canonical_request_fingerprint(
        command.owner_profile_id,
        command.thread_id,
        command.series_id,
        command.question,
        command.permission_scope_revision,
        command.corpus_access_scope,
        command.candidate_episodes,
    )
    return AgentJob(
        stable_agent_job_id(command.owner_profile_id, command.idempotency_key, fingerprint),
        command.owner_profile_id,
        command.thread_id,
        command.series_id,
        command.question,
        command.candidate_episodes,
        command.corpus_access_scope,
        command.permission_scope_revision,
        command.idempotency_key,
        fingerprint,
        datetime.now(UTC),
    )


class RefusalService:
    def __init__(self) -> None:
        self.calls = 0

    def execute(self, query):
        self.calls += 1
        return SeriesAgentResult(None, True)


class RejectingDispatcher:
    def dispatch(self, callback):
        return False

    def close(self):
        return None


def test_submit_executes_once_and_terminal_retry_is_deterministic() -> None:
    repository = InMemoryAgentJobRepository()
    conversation = RefusalService()
    service = AgentJobService(repository, conversation, InlineAgentJobDispatcher())
    command = _command()
    job = service.submit(command)
    assert job.status.value == "safe_refusal"
    service.execute(job.job_id)
    assert conversation.calls == 1
    assert [
        event.sequence for event in service.events_after(job.job_id, command.owner_profile_id)
    ] == [1, 2, 3]


def test_same_owner_key_conflict_and_cross_owner_isolation() -> None:
    repository = InMemoryAgentJobRepository()
    first = _command("00000000-0000-0000-0000-000000000001")
    service = AgentJobService(repository, RefusalService(), InlineAgentJobDispatcher())
    job = service.submit(first)
    changed = replace(first, question="A different bounded question")
    with pytest.raises(AgentJobIdempotencyConflictError):
        service.submit(changed)
    assert repository.get(job.job_id, uuid4()) is None


def test_claim_is_single_winner_under_race() -> None:
    repository = InMemoryAgentJobRepository()
    command = _command()
    job = _job(command)
    repository.create(job)
    barrier = Barrier(8)

    def claim():
        barrier.wait()
        return repository.claim_with_event(job.job_id)

    with ThreadPoolExecutor(max_workers=8) as pool:
        winners = tuple(pool.map(lambda _: claim(), range(8)))
    assert sum(item is not None for item in winners) == 1
    assert [event.kind for event in repository.list_events_after(job.job_id)] == [
        AgentJobEventKind.QUEUED,
        AgentJobEventKind.RUNNING,
    ]


def test_dispatcher_releases_capacity_after_callback_failure_and_close_is_idempotent() -> None:
    dispatcher = BoundedThreadPoolAgentJobDispatcher(max_workers=1, max_pending=1)
    started = Event()
    release = Event()
    failed = Event()

    def failing_callback() -> None:
        started.set()
        release.wait(timeout=2)
        failed.set()
        raise RuntimeError("test callback failure")

    assert dispatcher.dispatch(failing_callback)
    assert started.wait(timeout=2)
    assert dispatcher.dispatch(lambda: None) is False
    release.set()
    assert failed.wait(timeout=2)
    available = Event()
    assert dispatcher.dispatch(available.set)
    assert available.wait(timeout=2)
    dispatcher.close()
    dispatcher.close()
    assert dispatcher.dispatch(lambda: None) is False


def test_terminal_transition_rejects_direct_invalid_completion() -> None:
    repository = InMemoryAgentJobRepository()
    command = _command()
    service = AgentJobService(repository, RefusalService(), InlineAgentJobDispatcher())
    job = service.submit(command)
    with pytest.raises(AgentJobTransitionError):
        repository.complete_with_event(job.job_id, SeriesAgentResult(None, True))


def test_rejected_dispatch_is_a_coherent_failed_terminal_job() -> None:
    repository = InMemoryAgentJobRepository()
    command = _command()
    service = AgentJobService(repository, RefusalService(), RejectingDispatcher())
    job = service.submit(command)
    assert job.status.value == "failed"
    assert job.started_at is None
    assert job.finished_at is not None
    assert service.events_after(job.job_id, command.owner_profile_id)[-1].kind.value == "failed"


def test_terminal_transition_rolls_back_when_event_append_fails() -> None:
    repository = InMemoryAgentJobRepository()
    command = _command()

    class HoldingDispatcher:
        def dispatch(self, callback):
            return True

        def close(self):
            return None

    job_service = AgentJobService(repository, RefusalService(), HoldingDispatcher())
    job = job_service.submit(command)
    claimed = repository.claim_with_event(job.job_id)
    assert claimed is not None
    original = repository._append_locked

    def fail_append(*args, **kwargs):
        if args[1].value in {"succeeded", "safe_refusal", "failed"}:
            raise ValueError("injected event failure")
        return original(*args, **kwargs)

    repository._append_locked = fail_append
    with pytest.raises(ValueError):
        repository.fail_with_event(job.job_id, AgentJobErrorMessages.EXECUTION_FAILED)
    restored = repository.get(job.job_id)
    assert restored is not None and restored.status.value == "running"
    assert all(event.kind.value != "failed" for event in repository.list_events_after(job.job_id))


@pytest.mark.parametrize(
    "overrides",
    [
        {"question_min_length": 0},
        {"question_min_length": 5, "question_max_length": 4},
        {"candidate_max_episodes": 0},
        {"idempotency_key_max_length": 35},
        {"sse_poll_interval_seconds": 0.0},
        {"sse_replay_batch": 129},
        {
            "sse_poll_interval_seconds": 2.0,
            "sse_heartbeat_interval_seconds": 1.0,
        },
        {
            "sse_heartbeat_interval_seconds": 3.0,
            "sse_max_duration_seconds": 2.0,
        },
    ],
)
def test_agent_job_configuration_rejects_incoherent_limits(overrides) -> None:
    with pytest.raises(ValueError):
        AgentJobConfiguration(**overrides)


def test_service_normalizes_candidates_and_fingerprint_covers_security_inputs() -> None:
    first = _command()
    episode = first.candidate_episodes[0]
    second_episode = replace(
        episode,
        episode_id=uuid4(),
        position=replace(episode.position, episode_number=2),
    )
    ordered = replace(first, candidate_episodes=(episode, second_episode))
    reversed_command = replace(first, candidate_episodes=(second_episode, episode))
    first_fingerprint = canonical_request_fingerprint(
        ordered.owner_profile_id,
        ordered.thread_id,
        ordered.series_id,
        ordered.question,
        ordered.permission_scope_revision,
        ordered.corpus_access_scope,
        ordered.candidate_episodes,
    )
    reversed_fingerprint = canonical_request_fingerprint(
        reversed_command.owner_profile_id,
        reversed_command.thread_id,
        reversed_command.series_id,
        reversed_command.question,
        reversed_command.permission_scope_revision,
        reversed_command.corpus_access_scope,
        reversed_command.candidate_episodes,
    )
    assert first_fingerprint == reversed_fingerprint
    assert first_fingerprint != canonical_request_fingerprint(
        ordered.owner_profile_id,
        ordered.thread_id,
        ordered.series_id,
        "A different bounded question",
        ordered.permission_scope_revision,
        ordered.corpus_access_scope,
        ordered.candidate_episodes,
    )
    service = AgentJobService(
        InMemoryAgentJobRepository(), RefusalService(), InlineAgentJobDispatcher()
    )
    job = service.submit(reversed_command)
    assert job.candidate_episodes == (episode, second_episode)
    with pytest.raises(ValueError, match="unique"):
        service.submit(replace(first, candidate_episodes=(episode, episode)))


def test_event_payload_is_json_safe_private_and_recursively_immutable() -> None:
    event = AgentJobEvent(
        event_id=uuid4(),
        sequence=1,
        job_id=uuid4(),
        kind=AgentJobEventKind.QUEUED,
        occurred_at=datetime.now(UTC),
        payload={"status": "queued", "nested": {"values": [1, 2]}},
    )
    assert event.payload["nested"]["values"] == (1, 2)
    with pytest.raises(TypeError):
        event.payload["status"] = "changed"
    base = {
        "event_id": uuid4(),
        "sequence": 1,
        "job_id": uuid4(),
        "kind": AgentJobEventKind.QUEUED,
        "occurred_at": datetime.now(UTC),
    }
    for payload in (
        {"question": "private"},
        {"nested": {"provider": "openai"}},
        {"values": {1, 2}},
        {"score": float("nan")},
    ):
        with pytest.raises(ValueError):
            AgentJobEvent(**base, payload=payload)


def test_create_is_single_winner_and_events_are_owner_scoped_and_stable() -> None:
    command = _command()
    job = _job(command)
    barrier = Barrier(8)
    repository = InMemoryAgentJobRepository()

    def create():
        barrier.wait()
        return repository.create(job)

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = tuple(pool.map(lambda _: create(), range(8)))
    assert sum(created for _, created in results) == 1
    owner_events = repository.list_events_after(job.job_id, 0, job.owner_profile_id)
    assert len(owner_events) == 1
    assert repository.list_events_after(job.job_id, 0, uuid4()) == ()
    second_repository = InMemoryAgentJobRepository()
    second_repository.create(job)
    assert second_repository.list_events_after(job.job_id)[0].event_id == owner_events[0].event_id


def test_direct_submission_enforces_bounds_and_canonical_idempotency() -> None:
    service = AgentJobService(
        InMemoryAgentJobRepository(), RefusalService(), InlineAgentJobDispatcher()
    )
    command = _command()
    with pytest.raises(ValueError, match="canonical UUID"):
        service.submit(replace(command, idempotency_key=command.idempotency_key.upper()))
    with pytest.raises(ValueError, match="length"):
        service.submit(replace(command, question="x"))
    with pytest.raises(ValueError, match="limit"):
        service.submit(
            replace(
                command,
                candidate_episodes=command.candidate_episodes * 257,
            )
        )


def test_rejection_uses_only_the_sanitized_dispatch_failure_code() -> None:
    job = _job(_command())
    with pytest.raises(ValueError):
        job.reject("arbitrary_failure", datetime.now(UTC))
    rejected = job.reject(AgentJobErrorMessages.DISPATCH_UNAVAILABLE, datetime.now(UTC))
    assert rejected.started_at is None
    assert rejected.error_code == AgentJobErrorMessages.DISPATCH_UNAVAILABLE


@pytest.mark.parametrize(
    ("error", "expected_code"),
    [
        (AgentRuntimeBudgetExceeded(), RuntimeFailureCode.BUDGET_EXCEEDED.value),
        (
            AgentRuntimeFailure(RuntimeFailureCode.EXECUTION_TIMEOUT),
            RuntimeFailureCode.EXECUTION_TIMEOUT.value,
        ),
        (ConnectionError(), RuntimeFailureCode.PROVIDER_UNAVAILABLE.value),
        (ValueError("private provider detail"), AgentJobErrorMessages.EXECUTION_FAILED),
    ],
)
def test_runtime_failures_map_to_stable_private_event_codes(
    error: Exception, expected_code: str
) -> None:
    class FailingConversation:
        def execute(self, query):
            del query
            raise error

    repository = InMemoryAgentJobRepository()
    service = AgentJobService(repository, FailingConversation(), InlineAgentJobDispatcher())
    command = _command()

    job = service.submit(command)

    assert job.status.value == "failed"
    assert job.error_code == expected_code
    payload = dict(service.events_after(job.job_id, command.owner_profile_id)[-1].payload)
    assert payload == {"status": "failed", "error_code": expected_code}
    assert "private provider detail" not in str(payload)


def test_lifecycle_telemetry_is_exact_once_correlated_and_content_free() -> None:
    usage = ModelUsage(
        input_tokens=3,
        cached_input_tokens=1,
        output_tokens=2,
        total_tokens=5,
        cost_micros=7,
        model_role="synthesis",
        model_name="terra",
        response_identity="response-1",
    )
    ledger = ModelUsageLedger(
        entries=(usage,),
        input_tokens=3,
        cached_input_tokens=1,
        output_tokens=2,
        total_tokens=5,
        cost_micros=7,
    )

    class UsageConversation:
        def execute(self, query):
            del query
            observer = current_runtime_usage_observer()
            assert observer is not None
            observer(ledger)
            return SeriesAgentResult(None, True)

    class CollectingSink:
        def __init__(self) -> None:
            self.events = []

        def emit(self, event) -> None:
            self.events.append(event)

    sink = CollectingSink()
    times = iter((10.0, 10.25))
    service = AgentJobService(
        InMemoryAgentJobRepository(),
        UsageConversation(),
        InlineAgentJobDispatcher(),
        telemetry_sink=sink,
        monotonic_clock=lambda: next(times),
    )
    command = replace(_command(), request_id="request-123")

    job = service.submit(command)
    duplicate = service.submit(command)

    assert duplicate.job_id == job.job_id
    assert [event.stage for event in sink.events] == [
        TelemetryStage.QUEUED,
        TelemetryStage.RUNNING,
        TelemetryStage.MODEL,
        TelemetryStage.TERMINAL,
    ]
    assert sink.events[-1].outcome is TelemetryOutcome.SUCCESS
    assert sink.events[-1].duration_ms == 250.0
    assert sink.events[2].model_calls == 1
    assert sink.events[2].input_tokens == 3
    assert sink.events[2].output_tokens == 2
    assert sink.events[2].estimated_cost_micros == 7
    assert all(event.correlation_id == job.job_id for event in sink.events)
    assert all(event.request_id == "request-123" for event in sink.events)
    assert "A bounded question" not in repr(sink.events)


def test_dispatch_rejection_emits_sanitized_terminal_telemetry() -> None:
    class CollectingSink:
        def __init__(self) -> None:
            self.events = []

        def emit(self, event) -> None:
            self.events.append(event)

    sink = CollectingSink()
    times = iter((20.0, 20.01))
    service = AgentJobService(
        InMemoryAgentJobRepository(),
        RefusalService(),
        RejectingDispatcher(),
        telemetry_sink=sink,
        monotonic_clock=lambda: next(times),
    )

    job = service.submit(_command())

    assert job.status.value == "failed"
    assert [event.stage for event in sink.events] == [
        TelemetryStage.QUEUED,
        TelemetryStage.TERMINAL,
    ]
    assert sink.events[-1].failure_code == AgentJobErrorMessages.DISPATCH_UNAVAILABLE
    assert sink.events[-1].outcome is TelemetryOutcome.FAILURE
