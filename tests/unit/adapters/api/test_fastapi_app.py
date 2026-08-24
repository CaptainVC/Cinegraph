from pathlib import Path
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from tests.factories import DEFAULT_SERIES_ID

from cinegraph.adapters.api.context import ApiContext
from cinegraph.adapters.api.fastapi_app import create_app
from cinegraph.adapters.date_time.system_clock import SystemClock
from cinegraph.adapters.identity import (
    InMemoryIdentityUnitOfWorkFactory,
    ScryptPasswordHasher,
)
from cinegraph.application.exceptions.errors import SessionInvalidError
from cinegraph.application.models.episode_recommendation import (
    EpisodeRecommendation,
    RecommendEpisodesResult,
)
from cinegraph.application.models.hybrid_grounded_answer import (
    HybridGroundedAnswerResult,
)
from cinegraph.application.service.identity_session_service import (
    IdentitySessionService,
)
from cinegraph.config import CinegraphRuntimeSettings, RuntimeEnvironment
from cinegraph.config.transcript_chunking import TRANSCRIPT_INDEX_REVISION
from cinegraph.domain.enums.enum import (
    Language,
    PrincipalKind,
    RightsStatus,
    SpoilerMode,
)
from cinegraph.domain.models.catalogue.catalogue_manifest import CatalogueManifest
from cinegraph.domain.models.catalogue.episode import Episode
from cinegraph.domain.models.catalogue.season import Season
from cinegraph.domain.models.catalogue.series import Series
from cinegraph.ports.retrieval import RetrievedSegment


class SequenceTokenGenerator:
    def __init__(self) -> None:
        self.index = 0

    def generate(self) -> str:
        self.index += 1
        return f"api-session-token-{self.index:04d}"


class RecordingAnswerWorkflow:
    def __init__(self) -> None:
        self.queries = []

    def execute(self, query):
        self.queries.append(query)
        episode = query.candidate_episodes[0]
        citation = RetrievedSegment(
            segment_id=UUID(int=9001),
            source_version_id=UUID(int=9002),
            episode=episode,
            start_ms=1_000,
            end_ms=2_000,
            text="Phil introduces his family.",
            language=Language.ENGLISH,
            rights_status=RightsStatus.ALLOWED,
            score=0.92,
            member_segment_ids=(UUID(int=9001),),
            index_revision=TRANSCRIPT_INDEX_REVISION,
            ordinal=0,
        )
        return HybridGroundedAnswerResult(
            answer="Phil introduces his family.",
            citations=(citation,),
            is_safe_refusal=False,
        )


class RecordingRecommendationWorkflow:
    def __init__(self, catalogue: CatalogueManifest) -> None:
        self.queries = []
        self.episode = catalogue.episode_refs()[0]

    def execute(self, query):
        self.queries.append(query)
        citation = RetrievedSegment(
            segment_id=UUID(int=9101),
            source_version_id=UUID(int=9102),
            episode=self.episode,
            start_ms=2_000,
            end_ms=3_000,
            text="The family shares a playful dinner.",
            language=Language.ENGLISH,
            rights_status=RightsStatus.ALLOWED,
            score=0.89,
            member_segment_ids=(UUID(int=9101),),
            index_revision=TRANSCRIPT_INDEX_REVISION,
            ordinal=0,
        )
        return RecommendEpisodesResult(
            recommendations=(
                EpisodeRecommendation(
                    episode=self.episode,
                    episode_title="Season 1 premiere",
                    runtime_seconds=1_200,
                    score=0.9,
                    reason="The visible dinner scene matches the requested mood.",
                    citations=(citation,),
                ),
            ),
            visible_candidate_count=2,
        )


def make_catalogue() -> CatalogueManifest:
    seasons = []
    for season_number in (1, 2, 3):
        season_id = UUID(int=100 + season_number)
        episode = Episode(
            series_id=DEFAULT_SERIES_ID,
            season_id=season_id,
            episode_id=UUID(int=1_000 + season_number),
            episode_number=1,
            episode_title=f"Season {season_number} premiere",
        )
        seasons.append(
            Season(
                series_id=DEFAULT_SERIES_ID,
                season_id=season_id,
                season_number=season_number,
                episodes=(episode,),
            )
        )
    return CatalogueManifest(
        schema_version=1,
        series=(
            Series(
                series_id=DEFAULT_SERIES_ID,
                series_name="Modern Family",
                seasons=tuple(seasons),
            ),
        ),
    )


def make_context(tmp_path: Path, *, ready: bool = True):
    identity = IdentitySessionService(
        InMemoryIdentityUnitOfWorkFactory(),
        ScryptPasswordHasher(),
        SequenceTokenGenerator(),
        SystemClock(),
    )
    workflow = RecordingAnswerWorkflow()
    catalogue = make_catalogue()
    recommendation_workflow = RecordingRecommendationWorkflow(catalogue)
    context = ApiContext(
        settings=CinegraphRuntimeSettings(
            _env_file=None,
            knowledge_root=tmp_path,
            identity_database_path=tmp_path / "identity.sqlite3",
            qdrant_local_path=tmp_path / "qdrant",
        ),
        catalogue=catalogue,
        identity_sessions=identity,
        answer_workflow=workflow,
        readiness_probe=lambda: ready,
        recommendation_workflow=recommendation_workflow,
    )
    return context, workflow


def test_health_endpoints_distinguish_liveness_and_readiness(tmp_path: Path) -> None:
    context, _ = make_context(tmp_path, ready=False)
    with TestClient(create_app(context)) as client:
        assert client.get("/health/live").json() == {"status": "ok"}
        response = client.get("/health/ready")

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "service_unavailable"


def test_guest_cookie_and_catalogue_are_limited_to_seasons_one_and_two(
    tmp_path: Path,
) -> None:
    context, _ = make_context(tmp_path)
    with TestClient(create_app(context)) as client:
        session = client.post("/api/v1/auth/guest")
        catalogue = client.get("/api/v1/catalogue")

    assert session.status_code == 200
    assert session.json()["principal_kind"] == PrincipalKind.GUEST
    cookie = session.headers["set-cookie"]
    assert "HttpOnly" in cookie
    assert "SameSite=lax" in cookie
    assert [season["season_number"] for season in catalogue.json()["series"][0]["seasons"]] == [
        1,
        2,
    ]


def test_chat_uses_server_catalogue_and_session_scope_and_returns_provenance(
    tmp_path: Path,
) -> None:
    context, workflow = make_context(tmp_path)
    with TestClient(create_app(context)) as client:
        client.post("/api/v1/auth/guest")
        response = client.post(
            "/api/v1/chat",
            json={
                "series_id": str(DEFAULT_SERIES_ID),
                "question": "Who introduces the family?",
            },
        )

    assert response.status_code == 200
    assert response.json()["citations"][0]["source_version_id"] == str(UUID(int=9002))
    query = workflow.queries[0]
    assert len(query.candidate_episodes) == 3
    assert query.corpus_access_scope.revision == "guest-modern-family-s01-s02-v1"
    assert query.profile_watch_state.spoiler_mode is SpoilerMode.RELAXED


def test_sequential_chat_boundary_is_resolved_from_trusted_catalogue(
    tmp_path: Path,
) -> None:
    context, workflow = make_context(tmp_path)
    boundary_id = context.catalogue.series[0].seasons[1].episodes[0].episode_id
    with TestClient(create_app(context)) as client:
        client.post("/api/v1/auth/guest")
        response = client.post(
            "/api/v1/chat",
            json={
                "series_id": str(DEFAULT_SERIES_ID),
                "question": "What is safe through season two?",
                "spoiler_mode": "sequential",
                "safe_through_episode_id": str(boundary_id),
            },
        )

    assert response.status_code == 200
    state = workflow.queries[0].profile_watch_state
    assert state.spoiler_mode is SpoilerMode.SEQUENTIAL
    assert state.series_watch_states[0].sequential_safe_boundary.episode_id == boundary_id


def test_registration_duplicate_login_and_logout_have_stable_contracts(
    tmp_path: Path,
) -> None:
    context, _ = make_context(tmp_path)
    registration = {
        "email": "viewer@example.com",
        "password": "correct horse battery staple",
        "display_name": "Viewer",
    }
    with TestClient(create_app(context)) as client:
        created = client.post("/api/v1/auth/register", json=registration)
        duplicate = client.post("/api/v1/auth/register", json=registration)
        bad_login = client.post(
            "/api/v1/auth/login",
            json={"email": registration["email"], "password": "incorrect value"},
        )
        login = client.post(
            "/api/v1/auth/login",
            json={
                "email": registration["email"],
                "password": registration["password"],
            },
        )
        current = client.get("/api/v1/auth/session")
        logout = client.post("/api/v1/auth/logout")
        after_logout = client.get("/api/v1/auth/session")

    assert created.status_code == 201
    assert created.json()["principal_kind"] == PrincipalKind.AUTHENTICATED
    assert duplicate.status_code == 409
    assert bad_login.status_code == 401
    assert login.status_code == 200
    assert current.status_code == 200
    assert logout.status_code == 200
    assert after_logout.status_code == 401


def test_chat_and_catalogue_require_a_valid_session(tmp_path: Path) -> None:
    context, _ = make_context(tmp_path)
    with TestClient(create_app(context)) as client:
        catalogue = client.get("/api/v1/catalogue")
        chat = client.post(
            "/api/v1/chat",
            json={
                "series_id": str(DEFAULT_SERIES_ID),
                "question": "Who introduces the family?",
            },
        )
        recommendations = client.post(
            "/api/v1/recommendations",
            json={
                "series_id": str(DEFAULT_SERIES_ID),
                "mood": "warm",
            },
        )

    assert catalogue.status_code == 401
    assert chat.status_code == 401
    assert recommendations.status_code == 401


def test_recommendations_use_session_scope_and_return_visible_citations(
    tmp_path: Path,
) -> None:
    context, _ = make_context(tmp_path)
    workflow = context.recommendation_workflow
    with TestClient(create_app(context)) as client:
        client.post("/api/v1/auth/guest")
        response = client.post(
            "/api/v1/recommendations",
            json={
                "series_id": str(DEFAULT_SERIES_ID),
                "mood": "warm and playful",
                "characters": ["Alex"],
                "requested_count": 2,
            },
        )

    assert response.status_code == 200
    assert response.json()["visible_candidate_count"] == 2
    assert response.json()["recommendations"][0]["citations"][0]["source_version_id"] == str(
        UUID(int=9102)
    )
    query = workflow.queries[0]
    assert query.corpus_access_scope.revision == "guest-modern-family-s01-s02-v1"
    assert query.profile_watch_state.spoiler_mode is SpoilerMode.RELAXED


def test_production_seeds_host_csrf_cookie_and_requires_origin_and_double_submit(
    tmp_path: Path,
) -> None:
    context, _ = make_context(tmp_path)
    context.settings.environment = RuntimeEnvironment.PRODUCTION
    base_url = "https://cinegraph.example"
    with TestClient(create_app(context), base_url=base_url) as client:
        landing = client.get("/")
        csrf = client.cookies.get("__Host-cinegraph_csrf")
        missing_origin = client.post(
            "/api/v1/auth/guest",
            headers={"X-CSRF-Token": csrf or ""},
        )
        missing_header = client.post(
            "/api/v1/auth/guest",
            headers={"Origin": base_url},
        )
        guest = client.post(
            "/api/v1/auth/guest",
            headers={"Origin": base_url, "X-CSRF-Token": csrf or ""},
        )

    assert landing.status_code == 200
    assert csrf
    assert missing_origin.status_code == 403
    assert missing_origin.json()["error"]["code"] == "same_origin_required"
    assert missing_header.status_code == 403
    assert missing_header.json()["error"]["code"] == "csrf_failed"
    assert guest.status_code == 200
    assert "__Host-cinegraph_session=" in guest.headers["set-cookie"]
    assert "Secure" in guest.headers["set-cookie"]
    assert "Path=/" in guest.headers["set-cookie"]


def test_production_csrf_applies_to_authenticated_chat_and_logout_and_clears_host_cookies(
    tmp_path: Path,
) -> None:
    context, _ = make_context(tmp_path)
    context.settings.environment = RuntimeEnvironment.PRODUCTION
    base_url = "https://cinegraph.example"
    with TestClient(create_app(context), base_url=base_url) as client:
        client.get("/")
        csrf = client.cookies.get("__Host-cinegraph_csrf")
        client.post(
            "/api/v1/auth/guest",
            headers={"Origin": base_url, "X-CSRF-Token": csrf or ""},
        )
        csrf = client.cookies.get("__Host-cinegraph_csrf")
        missing = client.post(
            "/api/v1/chat",
            headers={"Origin": base_url},
            json={
                "series_id": str(DEFAULT_SERIES_ID),
                "question": "Who introduces the family?",
            },
        )
        chat = client.post(
            "/api/v1/chat",
            headers={"Origin": base_url, "X-CSRF-Token": csrf or ""},
            json={
                "series_id": str(DEFAULT_SERIES_ID),
                "question": "Who introduces the family?",
            },
        )
        logout = client.post(
            "/api/v1/auth/logout",
            headers={"Origin": base_url, "X-CSRF-Token": csrf or ""},
        )

    assert missing.status_code == 403
    assert missing.json()["error"]["code"] == "csrf_failed"
    assert chat.status_code == 200
    assert logout.status_code == 200
    assert '__Host-cinegraph_session="";' in logout.headers["set-cookie"]
    assert '__Host-cinegraph_csrf="";' in logout.headers["set-cookie"]


def test_production_sec_fetch_same_origin_is_accepted_and_csrf_rotation_is_required(
    tmp_path: Path,
) -> None:
    context, _ = make_context(tmp_path)
    context.settings.environment = RuntimeEnvironment.PRODUCTION
    with TestClient(
        create_app(context), base_url="https://cinegraph.example"
    ) as client:
        client.get("/")
        first_csrf = client.cookies.get("__Host-cinegraph_csrf")
        first_guest = client.post(
            "/api/v1/auth/guest",
            headers={
                "Sec-Fetch-Site": "same-origin",
                "X-CSRF-Token": first_csrf or "",
            },
        )
        second_csrf = client.cookies.get("__Host-cinegraph_csrf")
        stale = client.post(
            "/api/v1/auth/guest",
            headers={
                "Sec-Fetch-Site": "same-origin",
                "X-CSRF-Token": first_csrf or "",
            },
        )
        current = client.post(
            "/api/v1/auth/guest",
            headers={
                "Sec-Fetch-Site": "same-origin",
                "X-CSRF-Token": second_csrf or "",
            },
        )

    assert first_guest.status_code == 200
    assert second_csrf and second_csrf != first_csrf
    assert stale.status_code == 403
    assert stale.json()["error"]["code"] == "csrf_failed"
    assert current.status_code == 200


def test_guest_cannot_access_account_routes(tmp_path: Path) -> None:
    context, _ = make_context(tmp_path)
    with TestClient(create_app(context)) as client:
        client.post("/api/v1/auth/guest")
        account = client.get("/api/v1/account")
        sessions = client.get("/api/v1/account/sessions")
        profile = client.patch(
            "/api/v1/account/profile",
            json={"display_name": "Guest"},
        )

    assert account.status_code == 403
    assert sessions.status_code == 403
    assert profile.status_code == 403
    assert account.json()["error"]["code"] == "forbidden"
    assert account.json()["error"]["message"] == "An authenticated account is required."


def test_account_profile_password_and_session_routes_preserve_owner_isolation(
    tmp_path: Path,
) -> None:
    context, _ = make_context(tmp_path)
    password = "correct horse battery staple"
    with TestClient(create_app(context)) as first_client, TestClient(
        create_app(context)
    ) as second_client:
        first = first_client.post(
            "/api/v1/auth/register",
            json={
                "email": "first@example.com",
                "password": password,
                "display_name": "First viewer",
            },
        )
        second_client.post(
            "/api/v1/auth/register",
            json={
                "email": "second@example.com",
                "password": password,
                "display_name": "Second viewer",
            },
        )
        account = first_client.get("/api/v1/account")
        profile = first_client.patch(
            "/api/v1/account/profile",
            json={"display_name": "Renamed viewer"},
        )
        sessions = first_client.get("/api/v1/account/sessions")
        session_id = sessions.json()["sessions"][0]["session_id"]
        cross_user_revoke = second_client.delete(
            f"/api/v1/account/sessions/{session_id}"
        )
        unchanged_password = first_client.post(
            "/api/v1/account/password",
            json={"current_password": password, "new_password": password},
        )
        rotated = first_client.post(
            "/api/v1/account/password",
            json={
                "current_password": password,
                "new_password": "an entirely different password",
            },
        )
        logged_out_all = first_client.post("/api/v1/account/logout-all")
        after_logout_all = first_client.get("/api/v1/auth/session")

    assert first.status_code == 201
    assert account.status_code == 200
    assert account.json()["email"] == "first@example.com"
    assert profile.status_code == 200
    assert profile.json()["display_name"] == "Renamed viewer"
    assert sessions.status_code == 200
    assert cross_user_revoke.status_code == 404
    assert unchanged_password.status_code == 422
    assert rotated.status_code == 200
    assert logged_out_all.status_code == 200
    assert after_logout_all.status_code == 401


def test_auth_flows_rotate_presented_guest_and_current_sessions_and_expose_session_metadata(
    tmp_path: Path,
) -> None:
    context, _ = make_context(tmp_path)
    password = "correct horse battery staple"
    with TestClient(create_app(context)) as client:
        guest = client.post("/api/v1/auth/guest")
        guest_token = client.cookies.get("cinegraph_session")
        registered = client.post(
            "/api/v1/auth/register",
            json={
                "email": "rotate@example.com",
                "password": password,
                "display_name": "Rotate viewer",
            },
        )
        first_token = client.cookies.get("cinegraph_session")
        current = client.get("/api/v1/auth/session")
        logged_in = client.post(
            "/api/v1/auth/login",
            json={"email": "rotate@example.com", "password": password},
        )
        second_token = client.cookies.get("cinegraph_session")

    assert guest.status_code == 200
    assert registered.status_code == 201
    assert current.status_code == 200
    assert current.json()["display_name"] == "Rotate viewer"
    assert current.json()["expires_at"] is not None
    assert logged_in.status_code == 200
    assert second_token and second_token != first_token
    assert guest_token
    with pytest.raises(SessionInvalidError):
        context.identity_sessions.resolve(guest_token)
    assert first_token
    with pytest.raises(SessionInvalidError):
        context.identity_sessions.resolve(first_token)


def test_guest_current_session_exposes_expiry_without_token_or_account_metadata(
    tmp_path: Path,
) -> None:
    context, _ = make_context(tmp_path)
    with TestClient(create_app(context)) as client:
        issued = client.post("/api/v1/auth/guest")
        current = client.get("/api/v1/auth/session")

    assert issued.status_code == 200
    assert current.status_code == 200
    body = current.json()
    assert body["principal_kind"] == PrincipalKind.GUEST
    assert body["user_id"] is None
    assert body["display_name"] is None
    assert body["expires_at"] is not None
    assert "token" not in body


def test_deleting_current_session_revokes_it_and_clears_auth_cookies(
    tmp_path: Path,
) -> None:
    context, _ = make_context(tmp_path)
    with TestClient(create_app(context)) as client:
        client.post(
            "/api/v1/auth/register",
            json={
                "email": "delete-current@example.com",
                "password": "correct horse battery staple",
                "display_name": "Delete current",
            },
        )
        session_id = client.get("/api/v1/account/sessions").json()["sessions"][0][
            "session_id"
        ]
        deleted = client.delete(f"/api/v1/account/sessions/{session_id}")
        current = client.get("/api/v1/auth/session")

    assert deleted.status_code == 200
    assert "cinegraph_session=\"\";" in deleted.headers["set-cookie"]
    assert "cinegraph_csrf=\"\";" in deleted.headers["set-cookie"]
    assert current.status_code == 401
