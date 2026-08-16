from pathlib import Path
from uuid import UUID

from fastapi.testclient import TestClient

from cinegraph.adapters.api.context import ApiContext
from cinegraph.adapters.api.fastapi_app import create_app
from cinegraph.adapters.identity import (
    InMemorySessionRepository,
    InMemoryUserAccountRepository,
    ScryptPasswordHasher,
)
from cinegraph.adapters.date_time.system_clock import SystemClock
from cinegraph.application.models.hybrid_grounded_answer import (
    HybridGroundedAnswerResult,
)
from cinegraph.application.models.episode_recommendation import (
    EpisodeRecommendation,
    RecommendEpisodesResult,
)
from cinegraph.application.service.identity_session_service import (
    IdentitySessionService,
)
from cinegraph.config import CinegraphRuntimeSettings
from cinegraph.domain.enums.enum import Language, PrincipalKind, RightsStatus, SpoilerMode
from cinegraph.domain.models.catalogue.catalogue_manifest import CatalogueManifest
from cinegraph.domain.models.catalogue.episode import Episode
from cinegraph.domain.models.catalogue.season import Season
from cinegraph.domain.models.catalogue.series import Series
from cinegraph.ports.retrieval import RetrievedSegment
from tests.factories import DEFAULT_SERIES_ID


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
        InMemoryUserAccountRepository(),
        InMemorySessionRepository(),
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
    assert [
        season["season_number"]
        for season in catalogue.json()["series"][0]["seasons"]
    ] == [1, 2]


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
    assert response.json()["recommendations"][0]["citations"][0][
        "source_version_id"
    ] == str(UUID(int=9102))
    query = workflow.queries[0]
    assert query.corpus_access_scope.revision == "guest-modern-family-s01-s02-v1"
    assert query.profile_watch_state.spoiler_mode is SpoilerMode.RELAXED
