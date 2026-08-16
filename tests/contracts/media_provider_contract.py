from dataclasses import dataclass
from uuid import UUID

from cinegraph.application.models.media_provider import MediaProviderEpisode
from cinegraph.domain.enums.enum import MediaCommandKind
from cinegraph.domain.models.media_action import MediaCommand
from cinegraph.ports.media import MediaProvider


@dataclass(frozen=True, slots=True)
class MediaProviderContractContext:
    connection_id: UUID
    profile_id: UUID
    provider_owner_user_id: UUID
    episodes: tuple[MediaProviderEpisode, ...]


def assert_media_provider_contract(
    provider: MediaProvider,
    context: MediaProviderContractContext,
) -> None:
    health = provider.health(context.connection_id)
    assert health.available
    assert health.provider_label
    assert provider.connection_revision(context.connection_id) == (
        health.connection_revision
    )
    assert provider.list_library(context.connection_id, context.profile_id) == tuple(
        sorted(context.episodes, key=lambda item: item.episode_id)
    )

    episode_ids = tuple(episode.episode_id for episode in context.episodes)
    mark_watched = _command(
        context,
        health.connection_revision,
        MediaCommandKind.MARK_WATCHED,
        1,
        (episode_ids[0],),
    )
    watched_result = provider.execute(mark_watched)
    assert provider.verify(mark_watched, watched_result)
    replay = provider.execute(mark_watched)
    assert replay.idempotent_replay
    assert replay.external_reference == watched_result.external_reference

    favorite = _command(
        context,
        health.connection_revision,
        MediaCommandKind.SET_FAVORITE,
        2,
        (episode_ids[1],),
        favorite=True,
    )
    favorite_result = provider.execute(favorite)
    assert provider.verify(favorite, favorite_result)

    playlist = _command(
        context,
        health.connection_revision,
        MediaCommandKind.CREATE_PLAYLIST,
        3,
        episode_ids,
        playlist_name="Contract playlist",
    )
    playlist_result = provider.execute(playlist)
    assert provider.verify(playlist, playlist_result)

    playback = _command(
        context,
        health.connection_revision,
        MediaCommandKind.REQUEST_PLAYBACK,
        4,
        (episode_ids[0],),
    )
    playback_result = provider.execute(playback)
    assert provider.verify(playback, playback_result)

    snapshot = provider.profile_snapshot(context.connection_id, context.profile_id)
    assert episode_ids[0] in snapshot.watched_episode_ids
    assert episode_ids[1] in snapshot.favorite_episode_ids
    assert snapshot.playlists[0].name == "Contract playlist"
    assert snapshot.playlists[0].episode_ids == episode_ids
    assert snapshot.playback_requests[-1].command_id == playback.command_id


def _command(
    context: MediaProviderContractContext,
    connection_revision: str,
    kind: MediaCommandKind,
    sequence: int,
    episode_ids: tuple[UUID, ...],
    *,
    playlist_name: str | None = None,
    favorite: bool | None = None,
) -> MediaCommand:
    return MediaCommand(
        command_id=UUID(int=800 + sequence),
        kind=kind,
        profile_id=context.profile_id,
        provider_connection_id=context.connection_id,
        provider_owner_user_id=context.provider_owner_user_id,
        provider_connection_revision=connection_revision,
        idempotency_key=f"provider-contract-{sequence}",
        episode_ids=episode_ids,
        playlist_name=playlist_name,
        favorite=favorite,
    )
