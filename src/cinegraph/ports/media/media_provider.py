from typing import Protocol
from uuid import UUID

from cinegraph.application.models.media_action import MediaActionResult
from cinegraph.domain.models.media_action import MediaCommand


class MediaProvider(Protocol):
    def connection_revision(self, provider_connection_id: UUID) -> str: ...

    def execute(self, command: MediaCommand) -> MediaActionResult: ...

    def verify(self, command: MediaCommand, result: MediaActionResult) -> bool: ...
