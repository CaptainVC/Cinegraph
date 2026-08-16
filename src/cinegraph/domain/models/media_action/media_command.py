import hashlib
import json
from dataclasses import dataclass
from uuid import UUID

from cinegraph.common.error_messages import MediaActionErrorMessages
from cinegraph.config.media_actions import DEFAULT_MEDIA_ACTION_CONFIGURATION
from cinegraph.domain.enums.enum import MediaCommandKind, MediaCommandRisk
from cinegraph.domain.exceptions.errors import InvalidModelError


@dataclass(frozen=True, slots=True)
class MediaCommand:
    command_id: UUID
    kind: MediaCommandKind
    profile_id: UUID
    provider_connection_id: UUID
    provider_owner_user_id: UUID
    provider_connection_revision: str
    idempotency_key: str
    episode_ids: tuple[UUID, ...]
    playlist_name: str | None = None
    favorite: bool | None = None

    def __post_init__(self) -> None:
        identifiers = (
            self.command_id,
            self.profile_id,
            self.provider_connection_id,
            self.provider_owner_user_id,
        )
        if any(not isinstance(value, UUID) for value in identifiers):
            raise InvalidModelError(MediaActionErrorMessages.COMMAND_IDS_MUST_BE_VALID)
        if (
            not self.provider_connection_revision
            or self.provider_connection_revision.strip()
            != self.provider_connection_revision
        ):
            raise InvalidModelError(
                MediaActionErrorMessages.COMMAND_REVISION_MUST_BE_TRIMMED
            )
        if (
            not self.idempotency_key
            or self.idempotency_key.strip() != self.idempotency_key
            or len(self.idempotency_key)
            > DEFAULT_MEDIA_ACTION_CONFIGURATION.maximum_idempotency_key_length
        ):
            raise InvalidModelError(
                MediaActionErrorMessages.IDEMPOTENCY_KEY_MUST_BE_TRIMMED
            )
        if not isinstance(self.episode_ids, tuple):
            raise InvalidModelError(
                MediaActionErrorMessages.COMMAND_EPISODES_MUST_BE_IMMUTABLE
            )
        if (
            not self.episode_ids
            or any(not isinstance(value, UUID) for value in self.episode_ids)
            or len(set(self.episode_ids)) != len(self.episode_ids)
        ):
            raise InvalidModelError(
                MediaActionErrorMessages.COMMAND_EPISODES_MUST_BE_UNIQUE
            )
        self._validate_kind_parameters()

    @property
    def risk(self) -> MediaCommandRisk:
        if self.kind is MediaCommandKind.CREATE_PLAYLIST:
            return MediaCommandRisk.REVERSIBLE_MULTI_ITEM
        return MediaCommandRisk.REVERSIBLE_LOW_RISK

    @property
    def parameter_sha256(self) -> str:
        payload = json.dumps(
            {
                "command_id": str(self.command_id),
                "episode_ids": [str(value) for value in self.episode_ids],
                "favorite": self.favorite,
                "idempotency_key": self.idempotency_key,
                "kind": self.kind.value,
                "playlist_name": self.playlist_name,
                "profile_id": str(self.profile_id),
                "provider_connection_id": str(self.provider_connection_id),
                "provider_connection_revision": self.provider_connection_revision,
                "provider_owner_user_id": str(self.provider_owner_user_id),
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return hashlib.sha256(payload).hexdigest()

    @property
    def preview(self) -> str:
        if self.kind is MediaCommandKind.MARK_WATCHED:
            return f"Mark episode {self.episode_ids[0]} watched."
        if self.kind is MediaCommandKind.SET_FAVORITE:
            action = "Favorite" if self.favorite else "Unfavorite"
            return f"{action} episode {self.episode_ids[0]}."
        if self.kind is MediaCommandKind.REQUEST_PLAYBACK:
            return f"Request playback for episode {self.episode_ids[0]}."
        return (
            f"Create playlist '{self.playlist_name}' with "
            f"{len(self.episode_ids)} episodes."
        )

    def _validate_kind_parameters(self) -> None:
        single_episode = self.kind in {
            MediaCommandKind.MARK_WATCHED,
            MediaCommandKind.SET_FAVORITE,
            MediaCommandKind.REQUEST_PLAYBACK,
        }
        if single_episode and len(self.episode_ids) != 1:
            raise InvalidModelError(
                MediaActionErrorMessages.COMMAND_PARAMETERS_MUST_MATCH_KIND
            )
        if self.kind is MediaCommandKind.SET_FAVORITE:
            if not isinstance(self.favorite, bool) or self.playlist_name is not None:
                raise InvalidModelError(
                    MediaActionErrorMessages.COMMAND_PARAMETERS_MUST_MATCH_KIND
                )
            return
        if self.favorite is not None:
            raise InvalidModelError(
                MediaActionErrorMessages.COMMAND_PARAMETERS_MUST_MATCH_KIND
            )
        if self.kind is MediaCommandKind.CREATE_PLAYLIST:
            if (
                not self.playlist_name
                or self.playlist_name.strip() != self.playlist_name
                or len(self.playlist_name)
                > DEFAULT_MEDIA_ACTION_CONFIGURATION.maximum_playlist_name_length
                or len(self.episode_ids)
                > DEFAULT_MEDIA_ACTION_CONFIGURATION.maximum_playlist_items
            ):
                raise InvalidModelError(
                    MediaActionErrorMessages.PLAYLIST_NAME_MUST_BE_SAFE
                )
            return
        if self.playlist_name is not None:
            raise InvalidModelError(
                MediaActionErrorMessages.COMMAND_PARAMETERS_MUST_MATCH_KIND
            )
