from dataclasses import dataclass
from datetime import timedelta

from cinegraph.domain.enums.enum import MediaCommandKind


@dataclass(frozen=True, slots=True)
class MediaActionConfiguration:
    approval_ttl: timedelta
    permitted_command_kinds: frozenset[MediaCommandKind]
    maximum_playlist_items: int
    maximum_playlist_name_length: int
    maximum_idempotency_key_length: int

    def __post_init__(self) -> None:
        if self.approval_ttl <= timedelta(0):
            raise ValueError("Media action approval TTL must be positive.")
        if not self.permitted_command_kinds:
            raise ValueError("At least one media command kind must be permitted.")
        if min(
            self.maximum_playlist_items,
            self.maximum_playlist_name_length,
            self.maximum_idempotency_key_length,
        ) < 1:
            raise ValueError("Media action limits must be positive.")


DEFAULT_MEDIA_ACTION_CONFIGURATION = MediaActionConfiguration(
    approval_ttl=timedelta(minutes=15),
    permitted_command_kinds=frozenset(
        {
            MediaCommandKind.MARK_WATCHED,
            MediaCommandKind.SET_FAVORITE,
            MediaCommandKind.CREATE_PLAYLIST,
            MediaCommandKind.REQUEST_PLAYBACK,
        }
    ),
    maximum_playlist_items=100,
    maximum_playlist_name_length=120,
    maximum_idempotency_key_length=200,
)
