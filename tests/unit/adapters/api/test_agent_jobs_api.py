import asyncio
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from tests.factories import DEFAULT_SERIES_ID
from tests.unit.adapters.api.test_fastapi_app import make_context

from cinegraph.adapters.api.agent_jobs import AgentJobEventStream
from cinegraph.adapters.api.fastapi_app import create_app
from cinegraph.adapters.repository.in_memory.in_memory_agent_job_repository import (
    InMemoryAgentJobRepository,
)
from cinegraph.application.models.series_agent_result import (
    SeriesAgentCitation,
    SeriesAgentResult,
)
from cinegraph.application.service.agent_job_service import AgentJobService
from cinegraph.config import AgentJobConfiguration
from cinegraph.ports.agent_jobs.dispatcher import InlineAgentJobDispatcher


class RefusingConversation:
    def __init__(self) -> None:
        self.calls = []

    def execute(self, query):
        self.calls.append(query)
        return SeriesAgentResult(None, True)


class GroundedConversation:
    def __init__(self) -> None:
        self.calls = []

    def execute(self, query):
        self.calls.append(query)
        episode = query.candidate_episodes[0]
        return SeriesAgentResult(
            answer="Phil introduces the family.",
            is_safe_refusal=False,
            citations=(
                SeriesAgentCitation(
                    kind="transcript",
                    episode=episode,
                    start_ms=1_000,
                    end_ms=2_000,
                    segment_id=UUID(int=8_001),
                ),
            ),
            used_tools=("grounded_transcript_answer",),
        )


def _client(tmp_path: object, conversation=None) -> TestClient:
    context, _ = make_context(tmp_path)
    context.agent_job_service = AgentJobService(
        InMemoryAgentJobRepository(),
        conversation or RefusingConversation(),
        InlineAgentJobDispatcher(),
    )
    return TestClient(create_app(context))


def test_agent_jobs_require_auth_and_reject_extra_trusted_fields(tmp_path) -> None:
    with _client(tmp_path) as client:
        body = {
            "thread_id": str(uuid4()),
            "series_id": str(DEFAULT_SERIES_ID),
            "question": "Who?",
        }
        response = client.post(
            "/api/v1/agent/jobs",
            json=body,
            headers={"Idempotency-Key": str(uuid4())},
        )
        assert response.status_code == 401
        client.post("/api/v1/auth/guest")
        forbidden = {
            "profile_id": str(uuid4()),
            "candidate_episodes": [],
            "corpus_access_scope": {},
            "watch_state": {},
            "permission_scope_revision": "forged",
            "model": "forged",
            "tools": [],
            "limits": {},
        }
        for field, value in forbidden.items():
            response = client.post(
                "/api/v1/agent/jobs",
                json={**body, field: value},
                headers={"Idempotency-Key": str(uuid4())},
            )
            assert response.status_code == 422


def test_agent_job_idempotency_location_and_privacy_minimal_refusal(tmp_path) -> None:
    conversation = RefusingConversation()
    key = str(uuid4())
    body = {"thread_id": str(uuid4()), "series_id": str(DEFAULT_SERIES_ID), "question": "Who?"}
    with _client(tmp_path, conversation) as client:
        client.post("/api/v1/auth/guest")
        first = client.post("/api/v1/agent/jobs", json=body, headers={"Idempotency-Key": key})
        second = client.post("/api/v1/agent/jobs", json=body, headers={"Idempotency-Key": key})
        assert first.status_code == second.status_code == 202
        assert first.json()["job_id"] == second.json()["job_id"]
        assert first.headers["location"].startswith("http")
        payload = client.get(first.json()["status_url"]).json()
        assert len(conversation.calls) == 1
        assert "owner_profile_id" not in payload
        assert payload["status_url"].startswith("http")
        assert payload["events_url"].startswith("http")
        assert payload["result"]["is_safe_refusal"] is True
        assert payload["result"]["answer"] is None
        assert payload["result"]["citations"] == []


def test_changed_idempotent_request_conflicts_and_new_owner_is_isolated(tmp_path) -> None:
    key = str(uuid4())
    body = {
        "thread_id": str(uuid4()),
        "series_id": str(DEFAULT_SERIES_ID),
        "question": "Who?",
    }
    with _client(tmp_path) as client:
        client.post("/api/v1/auth/guest")
        first = client.post(
            "/api/v1/agent/jobs",
            json=body,
            headers={"Idempotency-Key": key},
        )
        conflict = client.post(
            "/api/v1/agent/jobs",
            json={**body, "question": "Where?"},
            headers={"Idempotency-Key": key},
        )
        assert conflict.status_code == 409
        client.post("/api/v1/auth/logout")
        client.post("/api/v1/auth/guest")
        other_owner = client.post(
            "/api/v1/agent/jobs",
            json=body,
            headers={"Idempotency-Key": key},
        )
        assert other_owner.status_code == 202
        assert other_owner.json()["job_id"] != first.json()["job_id"]


def test_malformed_idempotency_and_last_event_cursor_are_422(tmp_path) -> None:
    with _client(tmp_path) as client:
        client.post("/api/v1/auth/guest")
        body = {"thread_id": str(uuid4()), "series_id": str(DEFAULT_SERIES_ID), "question": "Who?"}
        malformed = client.post(
            "/api/v1/agent/jobs", json=body, headers={"Idempotency-Key": "not-a-uuid"}
        )
        assert malformed.status_code == 422
        missing = client.post("/api/v1/agent/jobs", json=body)
        assert missing.status_code == 422
        for noncanonical in (
            str(uuid4()).upper(),
            "{" + str(uuid4()) + "}",
            "urn:uuid:" + str(uuid4()),
        ):
            assert (
                client.post(
                    "/api/v1/agent/jobs", json=body, headers={"Idempotency-Key": noncanonical}
                ).status_code
                == 422
            )


def test_last_event_id_rejects_negative_leading_zero_and_huge_values(tmp_path) -> None:
    body = {"thread_id": str(uuid4()), "series_id": str(DEFAULT_SERIES_ID), "question": "Who?"}
    with _client(tmp_path) as client:
        client.post("/api/v1/auth/guest")
        created = client.post(
            "/api/v1/agent/jobs", json=body, headers={"Idempotency-Key": str(uuid4())}
        )
        events_url = created.json()["events_url"]
        for cursor in ("-1", "01", "129", "9" * 21):
            response = client.get(events_url, headers={"Last-Event-ID": cursor})
            assert response.status_code == 422


def test_job_resources_hide_unknown_and_cross_owner_jobs(tmp_path) -> None:
    body = {
        "thread_id": str(uuid4()),
        "series_id": str(DEFAULT_SERIES_ID),
        "question": "Who?",
    }
    with _client(tmp_path) as client:
        client.post("/api/v1/auth/guest")
        created = client.post(
            "/api/v1/agent/jobs",
            json=body,
            headers={"Idempotency-Key": str(uuid4())},
        ).json()
        unknown = client.get(f"/api/v1/agent/jobs/{uuid4()}")
        client.post("/api/v1/auth/logout")
        assert client.get(created["status_url"]).status_code == 401
        assert client.get(created["events_url"]).status_code == 401
        client.post("/api/v1/auth/guest")
        cross_status = client.get(created["status_url"])
        cross_events = client.get(created["events_url"])
        assert unknown.status_code == cross_status.status_code == cross_events.status_code == 404
        unknown_error = unknown.json()["error"]
        cross_status_error = cross_status.json()["error"]
        cross_events_error = cross_events.json()["error"]
        assert unknown_error["code"] == cross_status_error["code"] == cross_events_error["code"]
        assert unknown_error["message"] == cross_status_error["message"]
        assert cross_status_error["message"] == cross_events_error["message"]


def test_server_derives_guest_and_authenticated_candidates(tmp_path) -> None:
    conversation = RefusingConversation()
    body = {
        "thread_id": str(uuid4()),
        "series_id": str(DEFAULT_SERIES_ID),
        "question": "Who?",
    }
    with _client(tmp_path, conversation) as client:
        client.post("/api/v1/auth/guest")
        guest = client.post(
            "/api/v1/agent/jobs",
            json=body,
            headers={"Idempotency-Key": str(uuid4())},
        )
        assert guest.status_code == 202
        assert {
            item.position.season_number for item in conversation.calls[-1].candidate_episodes
        } == {1, 2}
        client.post("/api/v1/auth/logout")
        registration = {
            "email": "agent-viewer@example.com",
            "password": "correct horse battery staple",
            "display_name": "Agent Viewer",
        }
        assert client.post("/api/v1/auth/register", json=registration).status_code == 201
        authenticated = client.post(
            "/api/v1/agent/jobs",
            json={**body, "thread_id": str(uuid4())},
            headers={"Idempotency-Key": str(uuid4())},
        )
        assert authenticated.status_code == 202
        assert {
            item.position.season_number for item in conversation.calls[-1].candidate_episodes
        } == {1, 2, 3}


def test_unavailable_and_unknown_series_have_stable_errors(tmp_path) -> None:
    body = {
        "thread_id": str(uuid4()),
        "series_id": str(DEFAULT_SERIES_ID),
        "question": "Who?",
    }
    context, _ = make_context(tmp_path)
    with TestClient(create_app(context)) as client:
        client.post("/api/v1/auth/guest")
        unavailable = client.post(
            "/api/v1/agent/jobs",
            json=body,
            headers={"Idempotency-Key": str(uuid4())},
        )
        assert unavailable.status_code == 503
    with _client(tmp_path) as client:
        client.post("/api/v1/auth/guest")
        missing = client.post(
            "/api/v1/agent/jobs",
            json={**body, "series_id": str(uuid4())},
            headers={"Idempotency-Key": str(uuid4())},
        )
        assert missing.status_code == 404


def test_grounded_result_and_sse_expose_locators_without_transcript_text(tmp_path) -> None:
    body = {
        "thread_id": str(uuid4()),
        "series_id": str(DEFAULT_SERIES_ID),
        "question": "Who?",
    }
    with _client(tmp_path, GroundedConversation()) as client:
        client.post("/api/v1/auth/guest")
        created = client.post(
            "/api/v1/agent/jobs",
            json=body,
            headers={"Idempotency-Key": str(uuid4())},
        ).json()
        status_response = client.get(created["status_url"])
        result = status_response.json()["result"]
        assert result["answer"] == "Phil introduces the family."
        assert result["citations"][0]["segment_id"] == str(UUID(int=8_001))
        assert "text" not in result["citations"][0]
        events = client.get(created["events_url"])
        assert events.headers["cache-control"] == "no-cache, no-transform"
        assert events.headers["x-accel-buffering"] == "no"
        assert events.headers["content-type"].startswith("text/event-stream")
        assert '"text":' not in events.text
        replay = client.get(created["events_url"], headers={"Last-Event-ID": "1"})
        assert "id: 1\n" not in replay.text
        assert "id: 2\n" in replay.text and "id: 3\n" in replay.text


def test_async_sse_iterator_replays_compact_frames_and_closes_at_terminal(tmp_path) -> None:
    context, _ = make_context(tmp_path)
    conversation = RefusingConversation()
    repository = InMemoryAgentJobRepository()
    service = AgentJobService(repository, conversation, InlineAgentJobDispatcher())
    context.agent_job_service = service
    key = str(uuid4())
    body = {"thread_id": str(uuid4()), "series_id": str(DEFAULT_SERIES_ID), "question": "Who?"}
    with TestClient(create_app(context)) as client:
        client.post("/api/v1/auth/guest")
        created = client.post(
            "/api/v1/agent/jobs", json=body, headers={"Idempotency-Key": key}
        ).json()
    job_id = UUID(created["job_id"])
    owner = repository.get(job_id).owner_profile_id
    frames = []

    async def collect() -> None:
        stream = AgentJobEventStream(service, job_id, owner, 0, sleeper=lambda _: asyncio.sleep(0))
        async for frame in stream:
            frames.append(frame)

    asyncio.run(collect())
    assert [frame.split(b"\n", 2)[0] for frame in frames] == [b"id: 1", b"id: 2", b"id: 3"]
    assert b"question" not in b"".join(frames)


def test_async_sse_iterator_heartbeat_and_disconnect_are_injected() -> None:
    class EmptyService:
        def events_after(self, job_id, owner, sequence):
            return ()

    now = [0.0]
    disconnected = [False]

    async def sleeper(seconds):
        now[0] += seconds

    async def is_disconnected():
        return disconnected[0]

    config = AgentJobConfiguration(
        sse_poll_interval_seconds=0.5,
        sse_heartbeat_interval_seconds=0.5,
        sse_max_duration_seconds=2.0,
    )
    stream = AgentJobEventStream(
        EmptyService(),
        uuid4(),
        uuid4(),
        0,
        config,
        clock=lambda: now[0],
        sleeper=sleeper,
        disconnected=is_disconnected,
    )

    async def collect():
        return await stream.__anext__()

    assert asyncio.run(collect()) == b": heartbeat\n\n"
    disconnected[0] = True
    with pytest.raises(StopAsyncIteration):
        asyncio.run(collect())


def test_async_sse_iterator_live_follows_and_honors_event_and_duration_bounds(
    tmp_path,
) -> None:
    context, _ = make_context(tmp_path)
    repository = InMemoryAgentJobRepository()

    class HoldingDispatcher:
        def dispatch(self, callback):
            self.callback = callback
            return True

        def close(self):
            return None

    dispatcher = HoldingDispatcher()
    service = AgentJobService(repository, RefusingConversation(), dispatcher)
    context.agent_job_service = service
    with TestClient(create_app(context)) as client:
        client.post("/api/v1/auth/guest")
        created = client.post(
            "/api/v1/agent/jobs",
            json={
                "thread_id": str(uuid4()),
                "series_id": str(DEFAULT_SERIES_ID),
                "question": "Who?",
            },
            headers={"Idempotency-Key": str(uuid4())},
        ).json()
    job_id = UUID(created["job_id"])
    owner = repository.get(job_id).owner_profile_id
    slept = [False]

    async def execute_after_first_poll(_seconds):
        if not slept[0]:
            slept[0] = True
            dispatcher.callback()

    frames = []

    async def collect_live():
        stream = AgentJobEventStream(
            service,
            job_id,
            owner,
            0,
            sleeper=execute_after_first_poll,
        )
        async for frame in stream:
            frames.append(frame)

    asyncio.run(collect_live())
    assert [frame.split(b"\n", 1)[0] for frame in frames] == [
        b"id: 1",
        b"id: 2",
        b"id: 3",
    ]

    bounded = AgentJobEventStream(
        service,
        job_id,
        owner,
        0,
        configuration=AgentJobConfiguration(sse_max_events=2, sse_replay_batch=2),
        sleeper=lambda _seconds: asyncio.sleep(0),
    )

    async def collect_bounded():
        return [frame async for frame in bounded]

    assert len(asyncio.run(collect_bounded())) == 2

    now = [0.0]

    class EmptyService:
        def events_after(self, job_id, owner_profile_id, sequence):
            return ()

    async def advance(seconds):
        now[0] += seconds

    duration_limited = AgentJobEventStream(
        EmptyService(),
        uuid4(),
        uuid4(),
        0,
        configuration=AgentJobConfiguration(
            sse_poll_interval_seconds=0.5,
            sse_heartbeat_interval_seconds=2.0,
            sse_max_duration_seconds=2.0,
        ),
        clock=lambda: now[0],
        sleeper=advance,
    )

    async def collect_duration():
        return [frame async for frame in duration_limited]

    assert asyncio.run(collect_duration()) == []
