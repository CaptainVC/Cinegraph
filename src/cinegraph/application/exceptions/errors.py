
from uuid import UUID

from cinegraph.common.error_messages import (
    AccessErrorMessages,
    AuthenticationErrorMessages,
    ConversationErrorMessages,
    SourceErrorMessages,
    WatchErrorMessages,
    WorkflowErrorMessages,
)


class CorpusAccessDeniedError(PermissionError):

    def __init__(self) -> None:
        super().__init__(AccessErrorMessages.CORPUS_ACCESS_DENIED)


class AgentRuntimeContextInvalidError(ValueError):

    # Format the stable error for malformed or missing agent runtime context.
    def __init__(self) -> None:
        super().__init__(WorkflowErrorMessages.AGENT_RUNTIME_CONTEXT_MUST_BE_VALID)


class ConversationThreadProfileMismatchError(ValueError):

    # Format the error identifying a thread bound to another profile.
    def __init__(self, thread_id: UUID) -> None:
        super().__init__(ConversationErrorMessages.THREAD_PROFILE_MISMATCH.format(thread_id=thread_id))


class ConversationThreadWatchStateMismatchError(ValueError):

    # Format the error identifying a thread bound to another watch-state version.
    def __init__(self, thread_id: UUID) -> None:
        super().__init__(ConversationErrorMessages.THREAD_WATCH_STATE_MISMATCH.format(thread_id=thread_id))


class ConversationThreadScopeMismatchError(ValueError):

    # Format the error identifying a thread bound to another permission scope.
    def __init__(self, thread_id: UUID) -> None:
        super().__init__(ConversationErrorMessages.THREAD_SCOPE_MISMATCH.format(thread_id=thread_id))


class ProfileWatchStateNotFoundError(LookupError):

    # Format an error identifying the missing profile watch state.
    def __init__(self, profile_id: UUID) -> None:
        super().__init__(
            WatchErrorMessages
            .NO_WATCH_STATE_FOUND_FOR_PROFILE_ID
            .format(profile_id=profile_id)
        )


class SeasonNotFoundError(LookupError):
    # Format an error identifying the missing series season.
    def __init__(self, series_id: UUID, season_id: UUID) -> None:
        super().__init__(
            WatchErrorMessages.NO_SEASON_FOUND_FOR_SERIES_ID.format(
                series_id=series_id,
                season_id=season_id,
            )
        )

class SourceVersionNotFoundError(LookupError):
    # Format an error identifying the missing source version.
    def __init__(self, source_version_id: UUID) -> None:
        super().__init__(
            SourceErrorMessages.SOURCE_VERSION_NOT_FOUND.format(
                source_version_id=source_version_id,
            )
        )


class EmailAlreadyRegisteredError(ValueError):
    def __init__(self) -> None:
        super().__init__(AuthenticationErrorMessages.EMAIL_ALREADY_REGISTERED)


class InvalidCredentialsError(PermissionError):
    def __init__(self) -> None:
        super().__init__(AuthenticationErrorMessages.INVALID_CREDENTIALS)


class AccountDisabledError(PermissionError):
    def __init__(self) -> None:
        super().__init__(AuthenticationErrorMessages.ACCOUNT_DISABLED)


class SessionInvalidError(PermissionError):
    def __init__(self) -> None:
        super().__init__(AuthenticationErrorMessages.SESSION_INVALID)


class AccountRequiredError(PermissionError):
    def __init__(self) -> None:
        super().__init__(AuthenticationErrorMessages.ACCOUNT_REQUIRED)
