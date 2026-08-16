from typing import Protocol
from uuid import UUID

from cinegraph.application.models.media_action import MediaActionResult
from cinegraph.application.models.media_provider import (
    MediaProviderEpisode,
    MediaProviderHealth,
    MediaProviderProfileSnapshot,
)
from cinegraph.domain.models.media_action import MediaCommand


class MediaProvider(Protocol):
    def health(self, provider_connection_id: UUID) -> MediaProviderHealth: ...

    def connection_revision(self, provider_connection_id: UUID) -> str: ...

    def list_library(
        self,
        provider_connection_id: UUID,
        profile_id: UUID,
    ) -> tuple[MediaProviderEpisode, ...]: ...

    def profile_snapshot(
        self,
        provider_connection_id: UUID,
        profile_id: UUID,
    ) -> MediaProviderProfileSnapshot: ...

    def execute(self, command: MediaCommand) -> MediaActionResult: ...

    def verify(self, command: MediaCommand, result: MediaActionResult) -> bool: ...
