import hashlib
import logging
import secrets
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from hmac import compare_digest
from pathlib import Path
from uuid import UUID

from fastapi import Depends, FastAPI, HTTPException, Request, Response, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse
from fastapi.responses import Response as FastApiResponse
from fastapi.staticfiles import StaticFiles
from starlette.responses import JSONResponse

from cinegraph.adapters.api.agent_jobs import register_agent_job_routes
from cinegraph.adapters.api.context import ApiContext, build_default_api_context
from cinegraph.adapters.api.guardrails import (
    ApiGuardrailMiddleware,
    ApiGuardrailServices,
    error_response,
)
from cinegraph.adapters.api.schemas import (
    AccountResponse,
    CatalogueCreditResponse,
    CatalogueEpisodeResponse,
    CataloguePosterResponse,
    CatalogueResponse,
    CatalogueSeasonResponse,
    CatalogueSeriesResponse,
    ChatRequest,
    ChatResponse,
    CitationResponse,
    EpisodeRecommendationResponse,
    HealthResponse,
    LoginRequest,
    MessageResponse,
    MetadataSourceResponse,
    PasswordChangeRequest,
    ProfileUpdateRequest,
    RecommendationRequest,
    RecommendationResponse,
    RegisterRequest,
    SessionListResponse,
    SessionResponse,
    SessionSummaryResponse,
)
from cinegraph.application.exceptions.errors import (
    AccountDisabledError,
    AccountRequiredError,
    EmailAlreadyRegisteredError,
    InvalidCredentialsError,
    SessionInvalidError,
)
from cinegraph.application.models.episode_recommendation import (
    RecommendEpisodesQuery,
)
from cinegraph.application.models.hybrid_grounded_answer import (
    HybridGroundedAnswerQuery,
)
from cinegraph.application.models.identity_sessions import (
    AccountSummary,
    AuthenticateAccountCommand,
    ChangePasswordCommand,
    RegisterAccountCommand,
    SessionGrant,
    UpdateDisplayNameCommand,
)
from cinegraph.common.error_messages import AuthenticationErrorMessages
from cinegraph.config import (
    DEFAULT_API_CONFIGURATION,
    DEFAULT_AUTHENTICATION_CONFIGURATION,
    ApiConfiguration,
    RuntimeEnvironment,
)
from cinegraph.domain.enums.enum import SpoilerMode
from cinegraph.domain.models.identity import SessionPrincipal
from cinegraph.domain.models.series_metadata import CreditedPerson, SeriesMetadataSnapshot
from cinegraph.domain.models.watch_state import ProfileWatchState, SeriesWatchState

LOGGER = logging.getLogger("cinegraph.api")
STATIC_ROOT = Path(__file__).parent / "static"

ERROR_CODE_BY_STATUS = {
    status.HTTP_401_UNAUTHORIZED: "unauthorized",
    status.HTTP_403_FORBIDDEN: "forbidden",
    status.HTTP_404_NOT_FOUND: "not_found",
    status.HTTP_409_CONFLICT: "conflict",
    status.HTTP_413_CONTENT_TOO_LARGE: "request_too_large",
    status.HTTP_422_UNPROCESSABLE_CONTENT: "invalid_request",
    status.HTTP_429_TOO_MANY_REQUESTS: "rate_limit_exceeded",
    status.HTTP_503_SERVICE_UNAVAILABLE: "service_unavailable",
}


def _context(request: Request) -> ApiContext:
    return request.app.state.cinegraph_context


def _is_production(request: Request) -> bool:
    return _context(request).settings.environment is RuntimeEnvironment.PRODUCTION


def _session_cookie_name(request: Request) -> str:
    return DEFAULT_AUTHENTICATION_CONFIGURATION.session_cookie_name_for(_is_production(request))


def _csrf_cookie_name(request: Request) -> str:
    return DEFAULT_AUTHENTICATION_CONFIGURATION.csrf_cookie_name_for(_is_production(request))


def _session_token_optional(request: Request) -> str | None:
    return request.cookies.get(_session_cookie_name(request))


def _session_token(request: Request) -> str:
    token = _session_token_optional(request)
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
        principal = _context(request).identity_sessions.resolve(token)
        request.state.principal_kind = principal.kind.value
        return principal
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
        key=DEFAULT_AUTHENTICATION_CONFIGURATION.session_cookie_name_for(
            settings_environment is RuntimeEnvironment.PRODUCTION
        ),
        value=grant.token,
        max_age=maximum_age,
        expires=grant.expires_at,
        path=DEFAULT_AUTHENTICATION_CONFIGURATION.session_cookie_path,
        secure=settings_environment is RuntimeEnvironment.PRODUCTION,
        httponly=True,
        samesite=DEFAULT_AUTHENTICATION_CONFIGURATION.session_cookie_same_site,
    )


def _set_csrf_cookie(response: Response, production: bool) -> None:
    response.set_cookie(
        key=DEFAULT_AUTHENTICATION_CONFIGURATION.csrf_cookie_name_for(production),
        value=secrets.token_urlsafe(DEFAULT_AUTHENTICATION_CONFIGURATION.csrf_token_bytes),
        path=DEFAULT_AUTHENTICATION_CONFIGURATION.session_cookie_path,
        secure=production,
        httponly=False,
        samesite=DEFAULT_AUTHENTICATION_CONFIGURATION.session_cookie_same_site,
    )


def _clear_auth_cookies(response: Response, production: bool) -> None:
    response.delete_cookie(
        key=DEFAULT_AUTHENTICATION_CONFIGURATION.session_cookie_name_for(production),
        path=DEFAULT_AUTHENTICATION_CONFIGURATION.session_cookie_path,
        secure=production,
        httponly=True,
        samesite=DEFAULT_AUTHENTICATION_CONFIGURATION.session_cookie_same_site,
    )
    response.delete_cookie(
        key=DEFAULT_AUTHENTICATION_CONFIGURATION.csrf_cookie_name_for(production),
        path=DEFAULT_AUTHENTICATION_CONFIGURATION.session_cookie_path,
        secure=production,
        httponly=False,
        samesite=DEFAULT_AUTHENTICATION_CONFIGURATION.session_cookie_same_site,
    )


def _same_origin_request(request: Request) -> bool:
    origin = request.headers.get("origin")
    expected = f"{request.url.scheme}://{request.url.netloc}"
    if origin is not None:
        return compare_digest(origin.rstrip("/"), expected.rstrip("/"))
    fetch_site = request.headers.get("sec-fetch-site")
    return fetch_site in DEFAULT_AUTHENTICATION_CONFIGURATION.trusted_same_origin_sec_fetch_sites


def _csrf_valid(request: Request) -> bool:
    cookie = request.cookies.get(_csrf_cookie_name(request))
    header = request.headers.get(DEFAULT_AUTHENTICATION_CONFIGURATION.csrf_header_name)
    return bool(cookie and header and compare_digest(cookie, header))


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
                                guest_cast=_episode_guest_cast(
                                    context.series_metadata.get(series.series_id),
                                    episode.episode_id,
                                ),
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
                    poster=_poster_descriptor(context, series.series_id, series.series_name),
                    regular_cast=_regular_cast(
                        context.series_metadata.get(series.series_id)
                    ),
                    metadata_source=_metadata_source(
                        context.series_metadata.get(series.series_id)
                    ),
                )
            )
    return CatalogueResponse(
        schema_version=context.catalogue.schema_version,
        corpus_scope_revision=principal.corpus_access_scope.revision,
        series=tuple(series_items),
    )


def _credit_response(credit: CreditedPerson) -> CatalogueCreditResponse:
    return CatalogueCreditResponse(
        name=credit.name,
        character_name=credit.character_name,
        credit_kind=credit.credit_kind.value,
        canonical_url=credit.canonical_url,
        character_canonical_url=credit.character_canonical_url,
    )


def _regular_cast(snapshot: SeriesMetadataSnapshot | None) -> tuple[CatalogueCreditResponse, ...]:
    if snapshot is None:
        return ()
    return tuple(_credit_response(item) for item in snapshot.regular_cast)


def _episode_guest_cast(
    snapshot: SeriesMetadataSnapshot | None,
    episode_id: UUID,
) -> tuple[CatalogueCreditResponse, ...]:
    if snapshot is None:
        return ()
    for episode in snapshot.episodes:
        if episode.episode.episode_id == episode_id:
            return tuple(_credit_response(item) for item in episode.guest_cast)
    return ()


def _metadata_source(snapshot: SeriesMetadataSnapshot | None) -> MetadataSourceResponse | None:
    if snapshot is None:
        return None
    return MetadataSourceResponse(
        provider_name=snapshot.provider_name,
        canonical_url=snapshot.canonical_url,
        attribution=snapshot.attribution,
        license_name=snapshot.license_name,
        license_url=snapshot.license_url,
    )


def _poster_descriptor(
    context: ApiContext,
    series_id: UUID,
    series_name: str,
) -> CataloguePosterResponse | None:
    snapshot = context.series_metadata.get(series_id)
    if snapshot is None or snapshot.poster is None:
        return None
    poster = snapshot.poster
    return CataloguePosterResponse(
        url=f"{DEFAULT_API_CONFIGURATION.api_prefix}/series/{series_id}/poster",
        alt=f"Poster for {series_name}",
        width=poster.width,
        height=poster.height,
        attribution=poster.attribution,
        license_name=poster.license_name,
        license_url=poster.license_url,
    )


def _visible_series(
    context: ApiContext,
    principal: SessionPrincipal,
    series_id: UUID,
) -> bool:
    episode_refs = {
        episode.episode_id: episode for episode in context.catalogue.episode_refs()
    }
    return any(
        episode.series_id == series_id
        and principal.corpus_access_scope.allows_episode(episode)
        for episode in episode_refs.values()
    )


def _poster_file_type(path: Path, maximum_bytes: int) -> tuple[str, bytes] | None:
    try:
        size = path.stat().st_size
    except (FileNotFoundError, OSError):
        return None
    if size < 12 or size > maximum_bytes:
        return None
    try:
        content = path.read_bytes()
    except (FileNotFoundError, OSError):
        return None
    if len(content) != size or len(content) > maximum_bytes:
        return None
    if content.startswith(b"\xff\xd8\xff"):
        return "image/jpeg", content
    if content.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png", content
    if content.startswith(b"RIFF") and content[8:12] == b"WEBP":
        return "image/webp", content
    return None


def _build_watch_state(
    context: ApiContext,
    principal: SessionPrincipal,
    chat: ChatRequest | RecommendationRequest,
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
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
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


def create_app(
    context: ApiContext | None = None,
    guardrail_services: ApiGuardrailServices | None = None,
    api_configuration: ApiConfiguration = DEFAULT_API_CONFIGURATION,
) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
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
    @app.middleware("http")
    async def enforce_browser_request_security(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        unsafe = request.method.upper() in DEFAULT_AUTHENTICATION_CONFIGURATION.unsafe_methods
        is_api = request.url.path.startswith(DEFAULT_API_CONFIGURATION.api_prefix)
        if unsafe and is_api and _is_production(request):
            if not _same_origin_request(request):
                return error_response(
                    status_code=status.HTTP_403_FORBIDDEN,
                    code="same_origin_required",
                    message=AuthenticationErrorMessages.SAME_ORIGIN_REQUIRED,
                    request_id=getattr(request.state, "request_id", "unavailable"),
                )
            if not _csrf_valid(request):
                return error_response(
                    status_code=status.HTTP_403_FORBIDDEN,
                    code="csrf_failed",
                    message=AuthenticationErrorMessages.CSRF_TOKEN_REQUIRED,
                    request_id=getattr(request.state, "request_id", "unavailable"),
                )
        return await call_next(request)

    # Register guardrails after the browser-security middleware so Starlette's
    # reverse middleware construction places guardrails outermost.  That keeps
    # request IDs, security headers, rate limits, and audit events on early
    # Origin/CSRF rejections as well as normal responses.
    app.add_middleware(
        ApiGuardrailMiddleware,
        services=guardrail_services or ApiGuardrailServices.defaults(api_configuration),
        configuration=api_configuration,
    )

    app.mount("/assets", StaticFiles(directory=STATIC_ROOT), name="assets")
    prefix = DEFAULT_API_CONFIGURATION.api_prefix

    def current_request_id(request: Request) -> str:
        return getattr(request.state, "request_id", "unavailable")

    @app.exception_handler(HTTPException)
    async def handle_http_exception(
        request: Request,
        error: HTTPException,
    ) -> JSONResponse:
        message = error.detail if isinstance(error.detail, str) else "Request failed."
        return error_response(
            status_code=error.status_code,
            code=ERROR_CODE_BY_STATUS.get(error.status_code, "request_failed"),
            message=message,
            request_id=current_request_id(request),
            headers=dict(error.headers) if error.headers is not None else None,
        )

    @app.exception_handler(RequestValidationError)
    async def handle_validation_exception(
        request: Request,
        error: RequestValidationError,
    ) -> JSONResponse:
        fields = tuple(
            sorted(
                {
                    ".".join(str(part) for part in item["loc"] if part != "body")
                    or "request"
                    for item in error.errors()
                }
            )
        )
        return error_response(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            code="invalid_request",
            message="Request validation failed.",
            request_id=current_request_id(request),
            fields=fields,
        )

    @app.exception_handler(Exception)
    async def handle_unexpected_exception(
        request: Request,
        error: Exception,
    ) -> JSONResponse:
        LOGGER.error(
            "Unhandled API exception request_id=%s",
            current_request_id(request),
            exc_info=(type(error), error, error.__traceback__),
        )
        return error_response(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            code="internal_error",
            message="An internal error occurred.",
            request_id=current_request_id(request),
        )

    @app.get("/health/live", response_model=HealthResponse)
    def live() -> HealthResponse:
        return HealthResponse(status="ok")

    @app.get("/", include_in_schema=False, response_class=FileResponse)
    def product_ui(request: Request) -> FileResponse:
        # Seed the readable CSRF half of the double-submit pair before the
        # browser attempts its first unsafe authentication request.
        page = FileResponse(STATIC_ROOT / "index.html")
        if _csrf_cookie_name(request) not in request.cookies:
            _set_csrf_cookie(page, _is_production(request))
        return page

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
        try:
            grant = app_context.identity_sessions.issue_guest(_session_token_optional(request))
        except SessionInvalidError as error:
            # An authenticated session must never be downgraded to guest
            # access, including when its persisted entitlement is stale.
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail=AuthenticationErrorMessages.SESSION_INVALID,
            ) from error
        _set_session_cookie(response, grant, app_context.settings.environment)
        _set_csrf_cookie(response, _is_production(request))
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
                    current_session_token=_session_token_optional(request),
                )
            )
        except EmailAlreadyRegisteredError as error:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(error)) from error
        except ValueError as error:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail=str(error),
            ) from error
        _set_session_cookie(response, grant, app_context.settings.environment)
        _set_csrf_cookie(response, _is_production(request))
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
                AuthenticateAccountCommand(
                    email=body.email,
                    password=body.password,
                    current_session_token=_session_token_optional(request),
                )
            )
        except (InvalidCredentialsError, AccountDisabledError) as error:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail=AuthenticationErrorMessages.INVALID_CREDENTIALS,
            ) from error
        _set_session_cookie(response, grant, app_context.settings.environment)
        _set_csrf_cookie(response, _is_production(request))
        return _session_response(grant)

    @app.get(f"{prefix}/auth/session", response_model=SessionResponse)
    def session(
        request: Request,
        token: str = Depends(_session_token),
    ) -> SessionResponse:
        try:
            grant = _context(request).identity_sessions.resolve_grant(token)
        except SessionInvalidError as error:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail=AuthenticationErrorMessages.SESSION_INVALID,
            ) from error
        request.state.principal_kind = grant.principal.kind.value
        return _session_response(grant)

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
        _clear_auth_cookies(response, _is_production(request))
        return MessageResponse(message="Session ended.")

    def _account_response(account: AccountSummary) -> AccountResponse:
        return AccountResponse(
            user_id=account.user_id,
            profile_id=account.profile_id,
            email=account.email,
            display_name=account.display_name,
            status=account.status,
            created_at=account.created_at,
        )

    def _account_token_and_principal(request: Request) -> tuple[str, SessionPrincipal]:
        token = _session_token(request)
        try:
            principal = _context(request).identity_sessions.resolve(token)
        except SessionInvalidError as error:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=AuthenticationErrorMessages.SESSION_INVALID) from error
        if principal.user_id is None:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=AuthenticationErrorMessages.ACCOUNT_REQUIRED)
        return token, principal

    @app.get(f"{prefix}/account", response_model=AccountResponse)
    def account(request: Request) -> AccountResponse:
        token, _ = _account_token_and_principal(request)
        try:
            return _account_response(_context(request).identity_sessions.current_account(token))
        except AccountRequiredError as error:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=AuthenticationErrorMessages.ACCOUNT_REQUIRED) from error
        except SessionInvalidError as error:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=AuthenticationErrorMessages.SESSION_INVALID) from error

    @app.patch(f"{prefix}/account/profile", response_model=AccountResponse)
    def update_profile(body: ProfileUpdateRequest, request: Request) -> AccountResponse:
        token, _ = _account_token_and_principal(request)
        try:
            account = _context(request).identity_sessions.update_display_name(
                token, UpdateDisplayNameCommand(display_name=body.display_name)
            )
        except AccountRequiredError as error:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=AuthenticationErrorMessages.ACCOUNT_REQUIRED) from error
        except SessionInvalidError as error:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=AuthenticationErrorMessages.SESSION_INVALID) from error
        return _account_response(account)

    @app.post(f"{prefix}/account/password", response_model=SessionResponse)
    def change_password(body: PasswordChangeRequest, request: Request, response: Response) -> SessionResponse:
        token, _ = _account_token_and_principal(request)
        try:
            grant = _context(request).identity_sessions.change_password(
                token, ChangePasswordCommand(body.current_password, body.new_password)
            )
        except AccountRequiredError as error:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=AuthenticationErrorMessages.ACCOUNT_REQUIRED) from error
        except InvalidCredentialsError as error:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=AuthenticationErrorMessages.INVALID_CREDENTIALS) from error
        except SessionInvalidError as error:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=AuthenticationErrorMessages.SESSION_INVALID) from error
        except ValueError as error:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail=str(error),
            ) from error
        _clear_auth_cookies(response, _is_production(request))
        _set_session_cookie(response, grant, _context(request).settings.environment)
        _set_csrf_cookie(response, _is_production(request))
        return _session_response(grant)

    @app.get(f"{prefix}/account/sessions", response_model=SessionListResponse)
    def list_account_sessions(request: Request) -> SessionListResponse:
        token, _ = _account_token_and_principal(request)
        try:
            sessions = _context(request).identity_sessions.list_sessions(token)
        except SessionInvalidError as error:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=AuthenticationErrorMessages.SESSION_INVALID) from error
        return SessionListResponse(
            sessions=tuple(
                SessionSummaryResponse(
                    session_id=item.session_id,
                    created_at=item.created_at,
                    expires_at=item.expires_at,
                    current=item.current,
                )
                for item in sessions
            )
        )

    @app.delete(f"{prefix}/account/sessions/{{session_id}}", response_model=MessageResponse)
    def revoke_account_session(session_id: UUID, request: Request, response: Response) -> MessageResponse:
        token, _ = _account_token_and_principal(request)
        try:
            current = any(item.session_id == session_id and item.current for item in _context(request).identity_sessions.list_sessions(token))
        except SessionInvalidError as error:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=AuthenticationErrorMessages.SESSION_INVALID) from error
        try:
            revoked = _context(request).identity_sessions.revoke_session(token, session_id)
        except SessionInvalidError as error:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=AuthenticationErrorMessages.SESSION_INVALID) from error
        if not revoked:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=AuthenticationErrorMessages.SESSION_NOT_FOUND)
        if current:
            _clear_auth_cookies(response, _is_production(request))
        return MessageResponse(message="Session ended.")

    @app.post(f"{prefix}/account/logout-all", response_model=MessageResponse)
    def logout_all(request: Request, response: Response) -> MessageResponse:
        token, _ = _account_token_and_principal(request)
        try:
            _context(request).identity_sessions.revoke_all(token)
        except SessionInvalidError as error:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=AuthenticationErrorMessages.SESSION_INVALID) from error
        _clear_auth_cookies(response, _is_production(request))
        return MessageResponse(message="Sessions ended.")

    @app.get(f"{prefix}/catalogue", response_model=CatalogueResponse)
    def catalogue(
        request: Request,
        principal: SessionPrincipal = Depends(_principal),
    ) -> CatalogueResponse:
        return _catalogue_response(_context(request), principal)

    @app.get(f"{prefix}/series/{{series_id}}/poster")
    def series_poster(
        series_id: UUID,
        request: Request,
        principal: SessionPrincipal = Depends(_principal),
    ) -> FastApiResponse:
        app_context = _context(request)
        # The scope check intentionally precedes metadata lookup and path
        # resolution.  Hidden series therefore have the same response as a
        # missing or invalid poster and cannot be enumerated.
        if not _visible_series(app_context, principal, series_id):
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Poster not found.")
        snapshot = app_context.series_metadata.get(series_id)
        if snapshot is None or snapshot.poster is None or app_context.series_artwork_root is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Poster not found.")
        artwork_root = app_context.series_artwork_root
        try:
            root_resolved = artwork_root.resolve(strict=True)
            poster_path = (root_resolved / f"{series_id}.poster").resolve(strict=True)
            poster_path.relative_to(root_resolved)
        except (FileNotFoundError, OSError, ValueError):
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Poster not found."
            ) from None
        poster = _poster_file_type(
            poster_path, api_configuration.maximum_series_poster_bytes
        )
        if poster is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Poster not found.")
        media_type, content = poster
        etag = f'"{hashlib.sha256(content).hexdigest()}"'
        if request.headers.get("if-none-match") == etag:
            response = FastApiResponse(status_code=status.HTTP_304_NOT_MODIFIED)
            response.headers["Cache-Control"] = (
                api_configuration.series_poster_cache_control
            )
            response.headers["ETag"] = etag
            return response
        response = FastApiResponse(content=content, media_type=media_type)
        response.headers["Cache-Control"] = api_configuration.series_poster_cache_control
        response.headers["ETag"] = etag
        response.headers["Content-Disposition"] = "inline"
        return response

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

    @app.post(f"{prefix}/recommendations", response_model=RecommendationResponse)
    def recommendations(
        body: RecommendationRequest,
        request: Request,
        principal: SessionPrincipal = Depends(_principal),
    ) -> RecommendationResponse:
        app_context = _context(request)
        if app_context.recommendation_workflow is None:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Recommendation workflow is unavailable.",
            )
        if not any(
            item.series_id == body.series_id
            for item in app_context.catalogue.series
        ):
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Series was not found.",
            )
        result = app_context.recommendation_workflow.execute(
            RecommendEpisodesQuery(
                series_id=body.series_id,
                mood=body.mood,
                characters=body.characters,
                excluded_themes=body.excluded_themes,
                watch_preference=body.watch_preference,
                requested_count=body.requested_count,
                profile_watch_state=_build_watch_state(
                    app_context,
                    principal,
                    body,
                ),
                corpus_access_scope=principal.corpus_access_scope,
                maximum_runtime_seconds=body.maximum_runtime_seconds,
            )
        )
        return RecommendationResponse(
            visible_candidate_count=result.visible_candidate_count,
            recommendations=tuple(
                EpisodeRecommendationResponse(
                    episode_id=item.episode.episode_id,
                    season_number=item.episode.position.season_number,
                    episode_number=item.episode.position.episode_number,
                    episode_title=item.episode_title,
                    runtime_seconds=item.runtime_seconds,
                    score=item.score,
                    reason=item.reason,
                    citations=tuple(
                        CitationResponse(
                            segment_id=citation.segment_id,
                            source_version_id=citation.source_version_id,
                            season_number=citation.episode.position.season_number,
                            episode_number=citation.episode.position.episode_number,
                            start_ms=citation.start_ms,
                            end_ms=citation.end_ms,
                            text=citation.text,
                            score=citation.score,
                        )
                        for citation in item.citations
                    ),
                )
                for item in result.recommendations
            ),
        )

    register_agent_job_routes(app, prefix)

    return app
