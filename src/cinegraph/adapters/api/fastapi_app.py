from contextlib import asynccontextmanager
from datetime import UTC, datetime
from uuid import UUID

from fastapi import Depends, FastAPI, HTTPException, Request, Response, status

from cinegraph.adapters.api.context import ApiContext, build_default_api_context
from cinegraph.adapters.api.schemas import (
    CatalogueEpisodeResponse,
    CatalogueResponse,
    CatalogueSeasonResponse,
    CatalogueSeriesResponse,
    ChatRequest,
    ChatResponse,
    CitationResponse,
    HealthResponse,
    LoginRequest,
    MessageResponse,
    RegisterRequest,
    SessionResponse,
)
from cinegraph.application.exceptions.errors import (
    AccountDisabledError,
    EmailAlreadyRegisteredError,
    InvalidCredentialsError,
    SessionInvalidError,
)
from cinegraph.application.models.hybrid_grounded_answer import (
    HybridGroundedAnswerQuery,
)
from cinegraph.application.models.identity_sessions import (
    AuthenticateAccountCommand,
    RegisterAccountCommand,
    SessionGrant,
)
from cinegraph.common.error_messages import AuthenticationErrorMessages
from cinegraph.config import (
    DEFAULT_API_CONFIGURATION,
    DEFAULT_AUTHENTICATION_CONFIGURATION,
    RuntimeEnvironment,
)
from cinegraph.domain.enums.enum import SpoilerMode
from cinegraph.domain.models.identity import SessionPrincipal
from cinegraph.domain.models.watch_state import ProfileWatchState, SeriesWatchState


def _context(request: Request) -> ApiContext:
    return request.app.state.cinegraph_context


def _session_token(request: Request) -> str:
    token = request.cookies.get(
        DEFAULT_AUTHENTICATION_CONFIGURATION.session_cookie_name
    )
    if token is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=AuthenticationErrorMessages.SESSION_INVALID,
        )
    return token


def _principal(
    request: Request,
    token: str = Depends(_session_token),
) -> SessionPrincipal:
    try:
        return _context(request).identity_sessions.resolve(token)
    except SessionInvalidError as error:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=AuthenticationErrorMessages.SESSION_INVALID,
        ) from error


def _session_response(grant: SessionGrant) -> SessionResponse:
    return SessionResponse(
        principal_kind=grant.principal.kind,
        profile_id=grant.principal.profile_id,
        user_id=grant.principal.user_id,
        corpus_scope_revision=grant.principal.corpus_access_scope.revision,
        expires_at=grant.expires_at,
        display_name=grant.account.display_name if grant.account is not None else None,
    )


def _set_session_cookie(
    response: Response,
    grant: SessionGrant,
    settings_environment: RuntimeEnvironment,
) -> None:
    maximum_age = max(
        0,
        int((grant.expires_at - datetime.now(UTC)).total_seconds()),
    )
    response.set_cookie(
        key=DEFAULT_AUTHENTICATION_CONFIGURATION.session_cookie_name,
        value=grant.token,
        max_age=maximum_age,
        expires=grant.expires_at,
        path=DEFAULT_AUTHENTICATION_CONFIGURATION.session_cookie_path,
        secure=settings_environment is RuntimeEnvironment.PRODUCTION,
        httponly=True,
        samesite=DEFAULT_AUTHENTICATION_CONFIGURATION.session_cookie_same_site,
    )


def _catalogue_response(
    context: ApiContext,
    principal: SessionPrincipal,
) -> CatalogueResponse:
    series_items = []
    episode_refs_by_id = {
        episode.episode_id: episode for episode in context.catalogue.episode_refs()
    }
    for series in context.catalogue.series:
        season_items = []
        for season in series.seasons:
            visible_episodes = tuple(
                episode
                for episode in season.episodes
                if principal.corpus_access_scope.allows_episode(
                    episode_refs_by_id[episode.episode_id]
                )
            )
            if visible_episodes:
                season_items.append(
                    CatalogueSeasonResponse(
                        season_id=season.season_id,
                        season_number=season.season_number,
                        episodes=tuple(
                            CatalogueEpisodeResponse(
                                episode_id=episode.episode_id,
                                episode_number=episode.episode_number,
                                episode_title=episode.episode_title,
                            )
                            for episode in visible_episodes
                        ),
                    )
                )
        if season_items:
            series_items.append(
                CatalogueSeriesResponse(
                    series_id=series.series_id,
                    series_name=series.series_name,
                    seasons=tuple(season_items),
                )
            )
    return CatalogueResponse(
        schema_version=context.catalogue.schema_version,
        corpus_scope_revision=principal.corpus_access_scope.revision,
        series=tuple(series_items),
    )


def _build_watch_state(
    context: ApiContext,
    principal: SessionPrincipal,
    chat: ChatRequest,
) -> ProfileWatchState:
    episode_refs = tuple(
        episode
        for episode in context.catalogue.episode_refs()
        if episode.series_id == chat.series_id
    )
    if chat.spoiler_mode is SpoilerMode.RELAXED:
        return ProfileWatchState(
            profile_id=principal.profile_id,
            profile_name="API session",
            spoiler_mode=SpoilerMode.RELAXED,
        )

    boundary = next(
        (
            episode
            for episode in episode_refs
            if episode.episode_id == chat.safe_through_episode_id
        ),
        None,
    )
    if boundary is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Spoiler boundary must identify an episode in the requested series.",
        )
    series_state = SeriesWatchState(
        series_id=chat.series_id,
        sequential_safe_boundary=(
            boundary if chat.spoiler_mode is SpoilerMode.SEQUENTIAL else None
        ),
        manually_allowed_episodes=(
            frozenset(
                episode
                for episode in episode_refs
                if episode.position <= boundary.position
            )
            if chat.spoiler_mode is SpoilerMode.STRICT
            else frozenset()
        ),
    )
    return ProfileWatchState(
        profile_id=principal.profile_id,
        profile_name="API session",
        series_watch_states=(series_state,),
        spoiler_mode=chat.spoiler_mode,
    )


def create_app(context: ApiContext | None = None) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        owned_context = context or build_default_api_context()
        app.state.cinegraph_context = owned_context
        try:
            yield
        finally:
            if context is None:
                owned_context.close()

    app = FastAPI(
        title=DEFAULT_API_CONFIGURATION.title,
        version=DEFAULT_API_CONFIGURATION.version,
        lifespan=lifespan,
    )
    prefix = DEFAULT_API_CONFIGURATION.api_prefix

    @app.get("/health/live", response_model=HealthResponse)
    def live() -> HealthResponse:
        return HealthResponse(status="ok")

    @app.get("/health/ready", response_model=HealthResponse)
    def ready(request: Request) -> HealthResponse:
        if not _context(request).readiness_probe():
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Application dependencies are not ready.",
            )
        return HealthResponse(status="ready")

    @app.post(f"{prefix}/auth/guest", response_model=SessionResponse)
    def issue_guest(request: Request, response: Response) -> SessionResponse:
        app_context = _context(request)
        grant = app_context.identity_sessions.issue_guest()
        _set_session_cookie(response, grant, app_context.settings.environment)
        return _session_response(grant)

    @app.post(
        f"{prefix}/auth/register",
        response_model=SessionResponse,
        status_code=status.HTTP_201_CREATED,
    )
    def register(
        body: RegisterRequest,
        request: Request,
        response: Response,
    ) -> SessionResponse:
        app_context = _context(request)
        try:
            grant = app_context.identity_sessions.register(
                RegisterAccountCommand(
                    email=body.email,
                    password=body.password,
                    display_name=body.display_name,
                )
            )
        except EmailAlreadyRegisteredError as error:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(error)) from error
        except ValueError as error:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=str(error),
            ) from error
        _set_session_cookie(response, grant, app_context.settings.environment)
        return _session_response(grant)

    @app.post(f"{prefix}/auth/login", response_model=SessionResponse)
    def login(
        body: LoginRequest,
        request: Request,
        response: Response,
    ) -> SessionResponse:
        app_context = _context(request)
        try:
            grant = app_context.identity_sessions.authenticate(
                AuthenticateAccountCommand(email=body.email, password=body.password)
            )
        except (InvalidCredentialsError, AccountDisabledError) as error:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail=AuthenticationErrorMessages.INVALID_CREDENTIALS,
            ) from error
        _set_session_cookie(response, grant, app_context.settings.environment)
        return _session_response(grant)

    @app.get(f"{prefix}/auth/session", response_model=SessionResponse)
    def session(principal: SessionPrincipal = Depends(_principal)) -> SessionResponse:
        return SessionResponse(
            principal_kind=principal.kind,
            profile_id=principal.profile_id,
            user_id=principal.user_id,
            corpus_scope_revision=principal.corpus_access_scope.revision,
        )

    @app.post(f"{prefix}/auth/logout", response_model=MessageResponse)
    def logout(
        request: Request,
        response: Response,
        token: str = Depends(_session_token),
    ) -> MessageResponse:
        try:
            _context(request).identity_sessions.revoke(token)
        except SessionInvalidError as error:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail=AuthenticationErrorMessages.SESSION_INVALID,
            ) from error
        response.delete_cookie(
            key=DEFAULT_AUTHENTICATION_CONFIGURATION.session_cookie_name,
            path=DEFAULT_AUTHENTICATION_CONFIGURATION.session_cookie_path,
        )
        return MessageResponse(message="Session ended.")

    @app.get(f"{prefix}/catalogue", response_model=CatalogueResponse)
    def catalogue(
        request: Request,
        principal: SessionPrincipal = Depends(_principal),
    ) -> CatalogueResponse:
        return _catalogue_response(_context(request), principal)

    @app.post(f"{prefix}/chat", response_model=ChatResponse)
    def chat(
        body: ChatRequest,
        request: Request,
        principal: SessionPrincipal = Depends(_principal),
    ) -> ChatResponse:
        app_context = _context(request)
        candidates = tuple(
            episode
            for episode in app_context.catalogue.episode_refs()
            if episode.series_id == body.series_id
        )
        if not candidates:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Series was not found.",
            )
        result = app_context.answer_workflow.execute(
            HybridGroundedAnswerQuery(
                question=body.question,
                series_id=body.series_id,
                candidate_episodes=candidates,
                profile_watch_state=_build_watch_state(
                    app_context,
                    principal,
                    body,
                ),
                corpus_access_scope=principal.corpus_access_scope,
                limit=body.limit,
            )
        )
        return ChatResponse(
            answer=result.answer,
            citations=tuple(
                CitationResponse(
                    segment_id=item.segment_id,
                    source_version_id=item.source_version_id,
                    season_number=item.episode.position.season_number,
                    episode_number=item.episode.position.episode_number,
                    start_ms=item.start_ms,
                    end_ms=item.end_ms,
                    text=item.text,
                    score=item.score,
                )
                for item in result.citations
            ),
            is_safe_refusal=result.is_safe_refusal,
        )

    return app
