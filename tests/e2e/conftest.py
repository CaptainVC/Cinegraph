"""Shared fixtures for the Phase 38 browser contract tests.

The browser talks to a real localhost HTTP origin serving the application's
actual static shell.  API responses are mocked at the page boundary so the
tests never need credentials, a database, Qdrant, an LLM, or the private
corpus.
"""

from __future__ import annotations

import json
import threading
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any
from uuid import UUID

import pytest
from playwright.sync_api import Browser, Page, Playwright, sync_playwright

from cinegraph.adapters.api.context import ApiContext
from cinegraph.adapters.api.fastapi_app import create_app
from cinegraph.adapters.date_time.system_clock import SystemClock
from cinegraph.adapters.identity import (
    InMemoryIdentityUnitOfWorkFactory,
    ScryptPasswordHasher,
)
from cinegraph.application.service.identity_session_service import IdentitySessionService
from cinegraph.config import CinegraphRuntimeSettings
from cinegraph.domain.models.catalogue.catalogue_manifest import CatalogueManifest
from cinegraph.domain.models.catalogue.episode import Episode
from cinegraph.domain.models.catalogue.season import Season
from cinegraph.domain.models.catalogue.series import Series

SERIES_ID = UUID("11111111-1111-5111-8111-111111111111")
EPISODE_S1_ID = UUID("11111111-1111-5111-8111-111111111101")
EPISODE_S2_ID = UUID("11111111-1111-5111-8111-111111111102")
PROFILE_ID = UUID("22222222-2222-5222-8222-222222222222")
JOB_ID = UUID("33333333-3333-5333-8333-333333333333")
THREAD_ID = UUID("44444444-4444-5444-8444-444444444444")


class _SequenceTokenGenerator:
    def generate(self) -> str:
        return "synthetic-session-token"


class _NoopWorkflow:
    def execute(self, _query: object) -> object:
        raise AssertionError("browser API routes should be intercepted by Playwright")


def _safe_context(tmp_path: Path) -> ApiContext:
    identity = IdentitySessionService(
        InMemoryIdentityUnitOfWorkFactory(),
        ScryptPasswordHasher(),
        _SequenceTokenGenerator(),
        SystemClock(),
    )
    season_id = UUID("aaaaaaaa-aaaa-5aaa-8aaa-aaaaaaaaaaaa")
    episode_id = UUID("bbbbbbbb-bbbb-5bbb-8bbb-bbbbbbbbbbbb")
    episode = Episode(
        series_id=SERIES_ID,
        season_id=season_id,
        episode_id=episode_id,
        episode_number=1,
        episode_title="Synthetic episode",
    )
    catalogue = CatalogueManifest(
        schema_version=1,
        series=(
            Series(
                series_id=SERIES_ID,
                series_name="Synthetic series",
                seasons=(
                    Season(
                        series_id=SERIES_ID,
                        season_id=season_id,
                        season_number=1,
                        episodes=(episode,),
                    ),
                ),
            ),
        ),
    )
    return ApiContext(
        settings=CinegraphRuntimeSettings(
            _env_file=None,
            knowledge_root=tmp_path,
            identity_database_path=tmp_path / "synthetic.sqlite3",
            qdrant_local_path=tmp_path / "synthetic-qdrant",
        ),
        catalogue=catalogue,
        identity_sessions=identity,
        answer_workflow=_NoopWorkflow(),
        readiness_probe=lambda: True,
    )


@pytest.fixture(scope="session")
def http_origin(tmp_path_factory: pytest.TempPathFactory) -> Iterator[str]:
    """Serve the real FastAPI static UI over an ephemeral localhost port."""

    uvicorn = pytest.importorskip("uvicorn")
    context = _safe_context(tmp_path_factory.mktemp("browser-context"))
    app = create_app(context)
    config = uvicorn.Config(
        app,
        host="127.0.0.1",
        port=0,
        log_level="error",
        lifespan="on",
    )
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, name="cinegraph-e2e-http", daemon=True)
    thread.start()
    deadline = time.monotonic() + 10
    while not server.started and thread.is_alive() and time.monotonic() < deadline:
        time.sleep(0.01)
    if not server.started:
        server.should_exit = True
        thread.join(timeout=3)
        context.close()
        raise RuntimeError("uvicorn did not start the browser test origin")
    socket = server.servers[0].sockets[0]
    port = socket.getsockname()[1]
    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        server.should_exit = True
        thread.join(timeout=5)
        context.close()


@pytest.fixture(scope="session")
def playwright_runtime() -> Iterator[tuple[Playwright, Browser]]:
    """Launch Chromium once; an unavailable browser must fail the release gate."""

    runtime = sync_playwright().start()
    browser: Browser | None = None
    try:
        browser = runtime.chromium.launch(headless=True)
        yield runtime, browser
    finally:
        if browser is not None:
            browser.close()
        runtime.stop()


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(
    item: pytest.Item,
    call: pytest.CallInfo,
) -> Iterator[None]:
    """Expose the call report to fixtures without capturing application data."""

    outcome = yield
    report = outcome.get_result()
    setattr(item, f"report_{report.when}", report)


@pytest.fixture
def page(
    request: pytest.FixtureRequest,
    http_origin: str,
    playwright_runtime: tuple[Playwright, Browser],
) -> Iterator[Page]:
    _, browser = playwright_runtime
    browser_context = browser.new_context(viewport={"width": 1280, "height": 900})
    browser_context.tracing.start(screenshots=True, snapshots=True, sources=False)
    browser_page = browser_context.new_page()
    # The test installs deterministic endpoint routes after this fixture is
    # created. Abort the app's eager bootstrap requests until those routes are
    # in place; static assets still come from the real localhost origin.
    browser_page.route("**/api/**", lambda route: route.abort())
    # Configuration is intentionally available even during the eager bootstrap
    # so a release build that loads runtime controls before the test's case
    # routes still receives bounded deterministic values.
    browser_page.route(
        "**/client-config",
        lambda route: json_response(
            route,
            {
                "api_prefix": "/api/v1",
                "agent_poll_interval_ms": 25,
                "agent_job_deadline_ms": 2_000,
            },
        ),
    )
    # Bootstrap also requests readiness and the current session before an
    # individual test can register its case-specific routes. Supply safe
    # landing-state defaults so bootstrap completes deterministically.
    browser_page.route(
        "**/health/ready",
        lambda route: json_response(route, {"status": "ready"}),
    )
    browser_page.route(
        "**/api/v1/auth/session",
        lambda route: json_response(
            route,
            {"error": {"code": "session_invalid", "message": "No session"}},
            status=401,
        ),
    )
    browser_page.goto(http_origin, wait_until="domcontentloaded")
    browser_page.locator("html[data-cinegraph-bootstrap='ready']").wait_for()
    yield browser_page
    report = getattr(request.node, "report_call", None)
    if report is not None and report.failed:
        artifact_root = Path("test-results") / "e2e"
        artifact_root.mkdir(parents=True, exist_ok=True)
        artifact_name = "".join(
            character if character.isalnum() else "-" for character in request.node.nodeid
        ).strip("-")
        browser_page.screenshot(path=artifact_root / f"{artifact_name}.png", full_page=True)
        browser_context.tracing.stop(path=artifact_root / f"{artifact_name}.zip")
    else:
        browser_context.tracing.stop()
    browser_context.close()


def json_response(route: Any, payload: object, *, status: int = 200) -> None:
    route.fulfill(
        status=status,
        content_type="application/json",
        body=json.dumps(payload, separators=(",", ":")),
    )
