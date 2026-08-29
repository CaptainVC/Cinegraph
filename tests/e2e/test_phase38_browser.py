"""High-value Phase 38 release-readiness checks for the Cinegraph UI."""

from __future__ import annotations

from collections import Counter
from uuid import UUID

import pytest
from tests.e2e.conftest import (
    EPISODE_S1_ID,
    EPISODE_S2_ID,
    JOB_ID,
    PROFILE_ID,
    SERIES_ID,
    THREAD_ID,
    json_response,
)

pytestmark = pytest.mark.e2e

SESSION = {
    "principal_kind": "guest",
    "profile_id": str(PROFILE_ID),
    "user_id": None,
    "corpus_scope_revision": "guest-synthetic-v1",
    "expires_at": None,
    "display_name": None,
}

CATALOGUE = {
    "schema_version": 1,
    "corpus_scope_revision": "guest-synthetic-v1",
    "series": [
        {
            "series_id": str(SERIES_ID),
            "series_name": "Modern Family",
            "poster": None,
            "regular_cast": [],
            "metadata_source": None,
            "seasons": [
                {
                    "season_id": "11111111-1111-5111-8111-111111111201",
                    "season_number": 1,
                    "episodes": [
                        {
                            "episode_id": str(EPISODE_S1_ID),
                            "episode_number": 1,
                            "episode_title": "Pilot",
                            "guest_cast": [],
                        }
                    ],
                },
                {
                    "season_id": "11111111-1111-5111-8111-111111111202",
                    "season_number": 2,
                    "episodes": [
                        {
                            "episode_id": str(EPISODE_S2_ID),
                            "episode_number": 1,
                            "episode_title": "The Old Wagon",
                            "guest_cast": [],
                        }
                    ],
                },
            ],
        }
    ],
}

DEFAULT_API_PREFIX = "/api/v1"
CLIENT_CONFIG = {
    "api_prefix": DEFAULT_API_PREFIX,
    "agent_poll_interval_ms": 25,
    "agent_job_deadline_ms": 2_000,
}


def _job_urls(api_prefix: str = DEFAULT_API_PREFIX) -> tuple[str, str]:
    # The page base URL is deliberately used by the application; the helper
    # keeps route payloads independent of the ephemeral port.
    return (
        f"{api_prefix}/agent/jobs/{JOB_ID}",
        f"{api_prefix}/agent/jobs/{JOB_ID}/events",
    )


def _configure_common_api(
    page,
    *,
    status: str = "queued",
    result: dict | None = None,
    api_prefix: str = DEFAULT_API_PREFIX,
):
    requests: list[str] = []
    page.on("request", lambda request: requests.append(request.url))

    client_config = {**CLIENT_CONFIG, "api_prefix": api_prefix}
    page.route("**/client-config", lambda route: json_response(route, client_config))
    page.route("**/health/ready", lambda route: json_response(route, {"status": "ready"}))
    page.route(
        f"**{api_prefix}/auth/session",
        lambda route: json_response(
            route, {"error": {"code": "session_invalid", "message": "No session"}}, status=401
        ),
    )
    page.route(f"**{api_prefix}/auth/guest", lambda route: json_response(route, SESSION))
    page.route(f"**{api_prefix}/catalogue", lambda route: json_response(route, CATALOGUE))

    status_url, events_url = _job_urls(api_prefix)
    page.route(
        f"**{api_prefix}/agent/jobs",
        lambda route: json_response(
            route,
            {
                "job_id": str(JOB_ID),
                "thread_id": str(THREAD_ID),
                "series_id": str(SERIES_ID),
                "status": "queued",
                "created_at": "2026-01-01T00:00:00Z",
                "started_at": None,
                "finished_at": None,
                "result": None,
                "error_code": None,
                "status_url": status_url,
                "events_url": events_url,
            },
            status=202,
        ),
    )
    page.route(
        f"**{status_url}",
        lambda route: json_response(
            route,
            {
                "job_id": str(JOB_ID),
                "thread_id": str(THREAD_ID),
                "series_id": str(SERIES_ID),
                "status": status,
                "created_at": "2026-01-01T00:00:00Z",
                "started_at": "2026-01-01T00:00:01Z",
                "finished_at": "2026-01-01T00:00:02Z"
                if status in {"succeeded", "safe_refusal", "failed"}
                else None,
                "result": result,
                "error_code": None,
                "status_url": status_url,
                "events_url": events_url,
            },
        ),
    )
    return requests


def _open_guest(page) -> None:
    page.get_by_role("button", name="Explore as guest").click()
    page.locator("#workspace-view").wait_for(state="visible")


def _submit_question(page) -> None:
    page.locator("#question-input").fill("What connects the family?")
    page.locator("#send-button").click()


def _result(
    *,
    evidence_url: str | None,
    safe_refusal: bool = False,
    citations: list[dict] | None = None,
) -> dict:
    return {
        "answer": None if safe_refusal else "Claire keeps the family connected.",
        "is_safe_refusal": safe_refusal,
        "used_tools": []
        if safe_refusal
        else ["grounded_transcript_answer", "authorized_graph_relationships"],
        "citations": [] if safe_refusal else (citations or []),
        "evidence_url": evidence_url,
    }


def _transcript_citation() -> dict:
    return {
        "citation_id": "55555555-5555-5555-8555-555555555555",
        "kind": "transcript",
        "episode_id": str(EPISODE_S1_ID),
        "season_number": 1,
        "episode_number": 1,
        "start_ms": 61_000,
        "end_ms": 63_000,
        "segment_id": "66666666-6666-5666-8666-666666666666",
        "claim_id": None,
        "evidence_id": None,
        "graph": None,
    }


def _graph_citation() -> dict:
    return {
        "citation_id": "77777777-7777-5777-8777-777777777777",
        "kind": "graph",
        "episode_id": str(EPISODE_S1_ID),
        "season_number": 1,
        "episode_number": 1,
        "start_ms": 70_000,
        "end_ms": 72_000,
        "segment_id": None,
        "claim_id": "88888888-8888-5888-8888-888888888888",
        "evidence_id": "99999999-9999-5999-8999-999999999999",
        "graph": {
            "subject": {
                "entity_id": str(PROFILE_ID),
                "kind": "character",
                "display_name": "Claire",
            },
            "predicate": "cares_for",
            "object": {"entity_id": str(SERIES_ID), "kind": "story", "display_name": "The family"},
            "polarity": "positive",
            "hop_distance": 1,
            "score": 0.91,
        },
    }


def test_guest_exactly_exposes_seasons_one_and_two(page) -> None:
    _configure_common_api(page)
    _open_guest(page)

    assert page.locator("#scope-detail").text_content() == "Modern Family · Seasons 1–2"
    assert page.locator("#season-list .season-chip").all_text_contents() == ["Season 1", "Season 2"]
    assert page.locator("#series-select option").count() == 1
    assert page.locator("#boundary-select option").count() == 2


def test_malformed_runtime_configuration_fails_closed(page) -> None:
    page.unroute("**/client-config")
    page.route(
        "**/client-config",
        lambda route: json_response(
            route,
            {
                "api_prefix": DEFAULT_API_PREFIX,
                "agent_poll_interval_ms": 25,
                "agent_job_deadline_ms": 2_000,
                "unexpected": "rejected",
            },
        ),
    )
    page.reload(wait_until="domcontentloaded")
    page.locator("html[data-cinegraph-bootstrap='failed']").wait_for()

    assert page.locator("#guest-start-button").is_disabled()
    assert page.locator("#sign-in-button").is_disabled()
    assert page.locator("#create-account-button").is_disabled()
    assert page.locator("#service-status-text").text_content() == "Unavailable"
    assert (
        page.locator("#toast").text_content()
        == "Cinegraph could not load its runtime configuration. Please try again."
    )


def test_injected_api_prefix_drives_jobs_and_evidence_validation(page) -> None:
    api_prefix = "/internal/api"
    citation = _transcript_citation()
    evidence_path = f"{api_prefix}/agent/jobs/{JOB_ID}/evidence"
    requests = _configure_common_api(
        page,
        status="succeeded",
        result=_result(evidence_url=evidence_path, citations=[citation]),
        api_prefix=api_prefix,
    )
    page.route(
        f"**{api_prefix}/agent/jobs/{JOB_ID}/events",
        lambda route: route.fulfill(
            content_type="text/event-stream",
            body='event: succeeded\ndata: {"status":"succeeded"}\n\n',
        ),
    )
    page.route(
        f"**{evidence_path}",
        lambda route: json_response(
            route,
            {
                "job_id": str(JOB_ID),
                "items": [
                    {
                        "citation_id": citation["citation_id"],
                        "excerpt": "The configured prefix remains same-origin.",
                    }
                ],
            },
        ),
    )

    page.reload(wait_until="domcontentloaded")
    page.locator("html[data-cinegraph-bootstrap='ready']").wait_for()
    _open_guest(page)
    _submit_question(page)
    page.get_by_role("heading", name="Evidence trail", exact=True).wait_for()

    assert page.locator(".evidence-transcript blockquote").text_content() == (
        "The configured prefix remains same-origin."
    )
    assert any(url.endswith(evidence_path) for url in requests)
    assert not any("/api/v1/" in url for url in requests)


def test_uuidv5_success_hydrates_transcript_and_graph_as_text(page) -> None:
    citations = [_transcript_citation(), _graph_citation()]
    evidence_path = f"/api/v1/agent/jobs/{JOB_ID}/evidence"
    requests = _configure_common_api(
        page, status="succeeded", result=_result(evidence_url=evidence_path, citations=citations)
    )
    page.route(
        f"**{evidence_path}",
        lambda route: json_response(
            route,
            {
                "job_id": str(JOB_ID),
                "items": [
                    {
                        "citation_id": citations[0]["citation_id"],
                        "excerpt": "<img src=x onerror=alert(1)>Claire checks in.",
                    },
                    {
                        "citation_id": citations[1]["citation_id"],
                        "excerpt": "Claire protects the family.",
                    },
                ],
            },
        ),
    )
    page.route(
        f"**/api/v1/agent/jobs/{JOB_ID}/events",
        lambda route: route.fulfill(
            content_type="text/event-stream",
            body='id: 1\nevent: succeeded\ndata: {"status":"succeeded"}\n\n',
        ),
    )
    _open_guest(page)
    _submit_question(page)
    page.get_by_role("heading", name="Evidence trail", exact=True).wait_for()

    assert UUID(str(JOB_ID)).version == 5
    assert (
        page.locator(".evidence-transcript blockquote").text_content()
        == "<img src=x onerror=alert(1)>Claire checks in."
    )
    assert page.locator(".evidence-transcript blockquote img").count() == 0
    assert page.locator(".evidence-relationship").count() == 1
    assert page.get_by_text("Claire", exact=True).count() >= 1
    assert any(url.endswith(f"/agent/jobs/{JOB_ID}/evidence") for url in requests)


def test_safe_refusal_never_hydrates_evidence(page) -> None:
    requests = _configure_common_api(
        page, status="safe_refusal", result=_result(evidence_url=None, safe_refusal=True)
    )
    page.route(
        f"**/api/v1/agent/jobs/{JOB_ID}/events",
        lambda route: route.fulfill(
            content_type="text/event-stream",
            body='id: 1\nevent: safe_refusal\ndata: {"status":"safe_refusal"}\n\n',
        ),
    )
    _open_guest(page)
    _submit_question(page)
    page.get_by_text("Safe refusal").wait_for()

    assert page.get_by_text("No authorized evidence was returned for this answer.").count() == 1
    assert not any(url.endswith(f"/agent/jobs/{JOB_ID}/evidence") for url in requests)
    assert page.locator(".evidence-card").count() == 0


def test_event_failure_uses_deterministic_polling_fallback(page) -> None:
    status_calls = Counter()
    requests = _configure_common_api(page)
    status_path = f"/api/v1/agent/jobs/{JOB_ID}"
    page.unroute(f"**{status_path}")

    def status(route):
        status_calls["status"] += 1
        terminal = status_calls["status"] > 1
        json_response(
            route,
            {
                "job_id": str(JOB_ID),
                "thread_id": str(THREAD_ID),
                "series_id": str(SERIES_ID),
                "status": "succeeded" if terminal else "queued",
                "created_at": "2026-01-01T00:00:00Z",
                "started_at": None,
                "finished_at": None,
                "result": None
                if not terminal
                else _result(
                    evidence_url=f"/api/v1/agent/jobs/{JOB_ID}/evidence",
                    citations=[_transcript_citation()],
                ),
                "error_code": None,
                "status_url": status_path,
                "events_url": f"{status_path}/events",
            },
        )

    evidence_path = f"/api/v1/agent/jobs/{JOB_ID}/evidence"
    page.route(f"**{status_path}", status)
    page.route(f"**{status_path}/events", lambda route: route.abort())
    page.route(
        f"**{evidence_path}",
        lambda route: json_response(
            route,
            {
                "job_id": str(JOB_ID),
                "items": [
                    {
                        "citation_id": _transcript_citation()["citation_id"],
                        "excerpt": "Claire checks in.",
                    }
                ],
            },
        ),
    )
    _open_guest(page)
    _submit_question(page)
    page.get_by_role("heading", name="Evidence trail", exact=True).wait_for()

    assert status_calls["status"] >= 2
    assert any(url.endswith("/events") for url in requests)


@pytest.mark.parametrize(
    "evidence_url", [None, "https://evil.example/steal", "/api/v1/agent/jobs/not-a-uuid/evidence"]
)
def test_malformed_or_missing_evidence_url_fails_closed(page, evidence_url: str | None) -> None:
    requests = _configure_common_api(
        page,
        status="succeeded",
        result=_result(evidence_url=evidence_url, citations=[_transcript_citation()]),
    )
    page.route(
        f"**/api/v1/agent/jobs/{JOB_ID}/events",
        lambda route: route.fulfill(
            content_type="text/event-stream",
            body='event: succeeded\\ndata: {"status":"succeeded"}\\n\\n',
        ),
    )
    _open_guest(page)
    _submit_question(page)
    page.get_by_text(
        "This answer is withheld because authorized evidence could not be loaded for the current scope."
    ).wait_for()

    assert page.get_by_text("Claire keeps the family connected.", exact=True).count() == 0
    assert not any(url.endswith("/evidence") for url in requests)


@pytest.mark.parametrize("viewport", [(375, 667), (844, 390)])
def test_mobile_layout_has_no_horizontal_overflow(
    page,
    viewport: tuple[int, int],
) -> None:
    _configure_common_api(page)
    width, height = viewport
    page.set_viewport_size({"width": width, "height": height})
    _open_guest(page)
    dimensions = page.evaluate(
        "({width: window.innerWidth, documentWidth: document.documentElement.scrollWidth, bodyWidth: document.body.scrollWidth})"
    )
    assert dimensions["documentWidth"] <= dimensions["width"]
    assert dimensions["bodyWidth"] <= dimensions["width"]


def test_mobile_scope_drawer_traps_focus_and_escape_restores_focus(page) -> None:
    _configure_common_api(page)
    page.set_viewport_size({"width": 390, "height": 844})
    _open_guest(page)
    page.locator("#mobile-scope-button").click()
    drawer = page.locator("#story-scope")
    assert drawer.get_attribute("role") == "dialog"
    assert drawer.get_attribute("aria-modal") == "true"
    assert drawer.get_attribute("aria-hidden") == "false"
    assert page.locator("#scope-close-button").evaluate(
        "element => element === document.activeElement"
    )
    page.keyboard.press("Escape")
    assert drawer.get_attribute("aria-hidden") == "true"
    assert page.locator("#mobile-scope-button").evaluate(
        "element => element === document.activeElement"
    )


def test_happy_path_has_no_unexpected_requests_or_console_errors(page) -> None:
    unexpected: list[str] = []
    console_errors: list[str] = []
    allowed_api = {
        "/health/ready",
        "/client-config",
        "/api/v1/auth/session",
        "/api/v1/auth/guest",
        "/api/v1/catalogue",
    }
    page.on(
        "request",
        lambda request: unexpected.append(request.url)
        if ("/api/" in request.url or request.url.endswith("/client-config"))
        and not any(request.url.endswith(path) for path in allowed_api)
        else None,
    )
    page.on(
        "console",
        lambda message: console_errors.append(message.text) if message.type == "error" else None,
    )
    _configure_common_api(page)
    _open_guest(page)

    assert unexpected == []
    assert console_errors == []
