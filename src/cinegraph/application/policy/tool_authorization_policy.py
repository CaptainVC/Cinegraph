from cinegraph.common.error_messages import MediaActionErrorMessages
from cinegraph.config import (
    DEFAULT_MEDIA_ACTION_CONFIGURATION,
    MediaActionConfiguration,
)
from cinegraph.domain.enums.enum import PrincipalKind
from cinegraph.domain.models.identity import SessionPrincipal
from cinegraph.domain.models.media_action import MediaCommand


class ToolAuthorizationPolicy:
    def __init__(
        self,
        configuration: MediaActionConfiguration = DEFAULT_MEDIA_ACTION_CONFIGURATION,
    ) -> None:
        self._configuration = configuration

    def require_authorized(
        self,
        principal: SessionPrincipal,
        command: MediaCommand,
    ) -> None:
        if principal.kind is not PrincipalKind.AUTHENTICATED or principal.user_id is None:
            raise PermissionError(
                MediaActionErrorMessages.AUTHENTICATED_PRINCIPAL_REQUIRED
            )
        if principal.profile_id != command.profile_id:
            raise PermissionError(MediaActionErrorMessages.PRINCIPAL_MUST_OWN_PROFILE)
        if principal.user_id != command.provider_owner_user_id:
            raise PermissionError(MediaActionErrorMessages.PRINCIPAL_MUST_OWN_PROVIDER)
        if command.kind not in self._configuration.permitted_command_kinds:
            raise PermissionError(MediaActionErrorMessages.COMMAND_KIND_NOT_ALLOWED)
