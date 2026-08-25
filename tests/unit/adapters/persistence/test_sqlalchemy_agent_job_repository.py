from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from threading import Barrier
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, inspect
from tests.unit.application.agent_jobs.test_agent_job_control import _command, _job

from cinegraph.adapters.persistence.agent_job_serialization import job_from_json, job_to_json
from cinegraph.adapters.persistence.base import PersistenceBase
from cinegraph.adapters.persistence.sqlalchemy_agent_job_repository import (
    AgentJobEventRow,
    AgentJobRow,
    SqlAlchemyAgentJobRepository,
)
from cinegraph.application.models.series_agent_result import (
    SeriesAgentCitation,
    SeriesAgentResult,
)
from cinegraph.ports.agent_jobs.agent_job_repository import (
    AgentJobIdempotencyConflictError,
    AgentJobTransitionError,
    AgentJobUnavailableError,
)


def _repo(tmp_path: Path) -> SqlAlchemyAgentJobRepository:
    engine = create_engine(
        f"sqlite:///{tmp_path / 'jobs.db'}", connect_args={"check_same_thread": False}
    )
    PersistenceBase.metadata.create_all(engine)
    return SqlAlchemyAgentJobRepository(engine, clock=lambda: datetime.now(UTC))


def test_sql_repository_round_trip_and_event_cursor(tmp_path: Path) -> None:
    repository = _repo(tmp_path)
    job = _job(_command())
    stored, created = repository.create(job)
    assert created and stored == job
    assert repository.create(job)[1] is False
    assert [event.kind.value for event in repository.list_events_after(job.job_id)] == ["queued"]
    running = repository.claim_with_event(job.job_id, job.owner_profile_id)
    assert running is not None
    done = repository.complete_with_event(
        job.job_id, SeriesAgentResult(None, True), job.owner_profile_id
    )
    assert done is not None and done.status.value == "safe_refusal"
    assert [event.sequence for event in repository.events_after(job.job_id)] == [1, 2, 3]
    assert repository.events_after(job.job_id, 2)[0].kind.value == "safe_refusal"


def test_sql_repository_owner_isolation_and_conflict(tmp_path: Path) -> None:
    repository = _repo(tmp_path)
    command = _command()
    job = _job(command)
    repository.create(job)
    assert repository.get(job.job_id, job.owner_profile_id) is not None
    assert repository.get(job.job_id, _command().owner_profile_id) is None


def test_sql_repository_rejects_malformed_cursor(tmp_path: Path) -> None:
    repository = _repo(tmp_path)
    job = _job(_command())
    repository.create(job)
    try:
        repository.list_events_after(job.job_id, -1)
    except ValueError:
        pass
    else:
        raise AssertionError("negative cursor accepted")


def test_metadata_matches_durable_invariants(tmp_path: Path) -> None:
    repository = _repo(tmp_path)
    engine = repository._session_factory.kw["bind"]
    assert {c["name"] for c in inspect(engine).get_unique_constraints("agent_jobs")} >= {
        "uq_agent_jobs_owner_key"
    }
    assert {c["name"] for c in inspect(engine).get_unique_constraints("agent_job_events")} >= {
        "uq_agent_job_events_sequence"
    }
    assert (
        inspect(engine).get_foreign_keys("agent_job_events")[0]["options"]["ondelete"] == "CASCADE"
    )
    assert {i["name"] for i in inspect(engine).get_indexes("agent_jobs")} >= {
        "ix_agent_jobs_owner_status_created",
        "ix_agent_jobs_status_created",
    }


def test_cross_owner_key_is_allowed_and_same_owner_conflicts(tmp_path: Path) -> None:
    repository = _repo(tmp_path)
    command = _command()
    first = _job(command)
    repository.create(first)
    second = _job(command)
    second = replace(second, job_id=uuid4(), owner_profile_id=uuid4())
    assert repository.create(second)[1]
    changed = replace(first, job_id=uuid4(), request_fingerprint="0" * 64)
    with pytest.raises(AgentJobIdempotencyConflictError):
        repository.create(changed)


def test_race_create_has_one_queued_event(tmp_path: Path) -> None:
    repository = _repo(tmp_path)
    job = _job(_command())
    barrier = Barrier(2)

    def create() -> tuple[object, bool]:
        barrier.wait()
        return repository.create(job)

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(create) for _ in range(2)]
        results = [future.result() for future in futures]
    assert sum(bool(item[1]) for item in results) == 1
    assert len(repository.events_after(job.job_id)) == 1


def test_race_claim_has_one_winner(tmp_path: Path) -> None:
    repository = _repo(tmp_path)
    job = _job(_command())
    repository.create(job)
    barrier = Barrier(2)

    def claim() -> object:
        barrier.wait()
        return repository.claim_with_event(job.job_id, job.owner_profile_id)

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(claim) for _ in range(2)]
        winners = [future.result() for future in futures]
    assert sum(item is not None for item in winners) == 1
    assert [event.kind.value for event in repository.events_after(job.job_id)] == [
        "queued",
        "running",
    ]


def test_terminal_transition_is_once_and_stale_worker_cannot_write(tmp_path: Path) -> None:
    repository = _repo(tmp_path)
    job = _job(_command())
    repository.create(job)
    repository.claim_with_event(job.job_id, job.owner_profile_id)
    result = SeriesAgentResult(None, True)
    assert repository.complete_with_event(job.job_id, result, job.owner_profile_id) is not None
    with pytest.raises(AgentJobTransitionError):
        repository.complete_with_event(job.job_id, result, job.owner_profile_id)
    assert [e.kind.value for e in repository.events_after(job.job_id)] == [
        "queued",
        "running",
        "safe_refusal",
    ]


def test_append_failure_rolls_back_state_and_event(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repository = _repo(tmp_path)
    job = _job(_command())
    repository.create(job)
    original = repository._append

    def fail(*args: object, **kwargs: object) -> None:
        raise RuntimeError("injected")

    monkeypatch.setattr(repository, "_append", fail)
    with pytest.raises(RuntimeError):
        repository.claim_with_event(job.job_id, job.owner_profile_id)
    monkeypatch.setattr(repository, "_append", original)
    assert repository.get(job.job_id).status.value == "queued"
    assert len(repository.events_after(job.job_id)) == 1


def test_tampered_question_scope_result_and_event_fail_closed(tmp_path: Path) -> None:
    repository = _repo(tmp_path)
    job = _job(_command())
    repository.create(job)
    with repository._session_factory.begin() as session:
        session.execute(
            AgentJobRow.__table__.update()
            .where(AgentJobRow.job_id == job.job_id)
            .values(question_json={})
        )
    with pytest.raises(AgentJobUnavailableError):
        repository.get(job.job_id)


def test_event_cursor_and_owner_reads_do_not_leak(tmp_path: Path) -> None:
    repository = _repo(tmp_path)
    job = _job(_command())
    repository.create(job)
    assert repository.get(job.job_id, uuid4()) is None
    assert repository.events_after(job.job_id, owner_profile_id=uuid4()) == ()
    assert repository.events_after(job.job_id, 1) == ()


def test_job_json_rejects_non_utc_and_duplicate_candidates() -> None:
    job = _job(_command())
    payload = job_to_json(job)
    payload["created_at"] = job.created_at.replace(tzinfo=None).isoformat()
    with pytest.raises(ValueError):
        job_from_json(payload)

    duplicate_payload = job_to_json(job)
    candidates = duplicate_payload["candidate_episodes"]
    assert isinstance(candidates, list)
    candidates.append(candidates[0])
    with pytest.raises(ValueError):
        job_from_json(duplicate_payload)


def test_refusal_event_payload_is_minimal(tmp_path: Path) -> None:
    repository = _repo(tmp_path)
    job = _job(_command())
    repository.create(job)
    repository.claim_with_event(job.job_id, job.owner_profile_id)
    repository.complete_with_event(job.job_id, SeriesAgentResult(None, True), job.owner_profile_id)
    payload = dict(repository.events_after(job.job_id)[-1].payload)
    assert payload == {"status": "safe_refusal", "safe_refusal": True}


def test_grounded_result_round_trips_with_authorized_sse_locators(tmp_path: Path) -> None:
    repository = _repo(tmp_path)
    job = _job(_command())
    episode = job.candidate_episodes[0]
    result = SeriesAgentResult(
        answer="A grounded answer.",
        is_safe_refusal=False,
        citations=(
            SeriesAgentCitation(
                kind="transcript",
                episode=episode,
                start_ms=1_000,
                end_ms=2_000,
                segment_id=uuid4(),
            ),
        ),
        used_tools=("grounded_transcript_answer",),
    )
    repository.create(job)
    repository.claim_with_event(job.job_id, job.owner_profile_id)
    completed = repository.complete_with_event(job.job_id, result, job.owner_profile_id)

    assert completed is not None
    assert repository.get(job.job_id, job.owner_profile_id) == completed
    payload = dict(repository.events_after(job.job_id)[-1].payload)
    assert payload["answer"] == "A grounded answer."
    assert payload["used_tools"] == ("grounded_transcript_answer",)
    citations = payload["citations"]
    assert isinstance(citations, tuple)
    assert citations[0]["episode_id"] == str(episode.episode_id)
    assert "text" not in citations[0]


def test_tampered_scope_result_and_event_rows_fail_closed(tmp_path: Path) -> None:
    scope_repository = _repo(tmp_path)
    scope_job = _job(_command())
    scope_repository.create(scope_job)
    with scope_repository._session_factory.begin() as session:
        scope = {
            "mode": "guest",
            "revision": scope_job.permission_scope_revision,
            "unrestricted": True,
            "allowed_seasons": [],
        }
        session.execute(
            AgentJobRow.__table__.update()
            .where(AgentJobRow.job_id == scope_job.job_id)
            .values(corpus_access_scope_json=scope)
        )
    with pytest.raises(AgentJobUnavailableError):
        scope_repository.get(scope_job.job_id)

    result_repository = scope_repository
    result_job = _job(_command())
    result_repository.create(result_job)
    result_repository.claim_with_event(result_job.job_id, result_job.owner_profile_id)
    result_repository.complete_with_event(
        result_job.job_id, SeriesAgentResult(None, True), result_job.owner_profile_id
    )
    with result_repository._session_factory.begin() as session:
        session.execute(
            AgentJobRow.__table__.update()
            .where(AgentJobRow.job_id == result_job.job_id)
            .values(result_json={"answer": "tampered"})
        )
    with pytest.raises(AgentJobUnavailableError):
        result_repository.get(result_job.job_id)

    event_repository = scope_repository
    event_job = _job(_command())
    event_repository.create(event_job)
    with event_repository._session_factory.begin() as session:
        session.execute(
            AgentJobEventRow.__table__.update()
            .where(AgentJobEventRow.job_id == event_job.job_id)
            .values(payload_json={"question": "private"})
        )
    with pytest.raises(AgentJobUnavailableError):
        event_repository.events_after(event_job.job_id)
