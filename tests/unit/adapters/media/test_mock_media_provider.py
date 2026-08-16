from dataclasses import replace
from uuid import UUID

import pytest

from cinegraph.adapters.media import (
    MockMediaProvider,
    MockMediaProviderProfileSeed,
    MockMediaProviderSeed,
)
from cinegraph.application.models.media_provider import MediaProviderEpisode
from cinegraph.common.error_messages import MediaProviderErrorMessages
from cinegraph.config import DEFAULT_MOCK_MEDIA_PROVIDER_CONFIGURATION
from cinegraph.domain.enums.enum import MediaCommandKind
from cinegraph.domain.models.media_action import MediaCommand
from tests.contracts.media_provider_contract import (
    MediaProviderContractContext,
    assert_media_provider_contract,
)


CONNECTION_ID = UUID(int=701)
PROFILE_ID = UUID(int=702)
OTHER_PROFILE_ID = UUID(int=703)
OWNER_ID = UUID(int=704)
EPISODE_ONE_ID = UUID(int=705)
EPISODE_TWO_ID = UUID(int=706)
EPISODES = (
    MediaProviderEpisode(EPISODE_ONE_ID, "mock-item-1", "Synthetic Pilot"),
    MediaProviderEpisode(EPISODE_TWO_ID, "mock-item-2", "Synthetic Follow-up"),
)


def seed() -> MockMediaProviderSeed:
    return MockMediaProviderSeed(
        connection_id=CONNECTION_ID,
        episodes=EPISODES,
        profiles=(
            MockMediaProviderProfileSeed(
                profile_id=PROFILE_ID,
                watched_episode_ids=frozenset({EPISODE_TWO_ID}),
            ),
        ),
    )


def command(**changes) -> MediaCommand:
    values = {
        "command_id": UUID(int=710),
        "kind": MediaCommandKind.MARK_WATCHED,
        "profile_id": PROFILE_ID,
        "provider_connection_id": CONNECTION_ID,
        "provider_owner_user_id": OWNER_ID,
        "provider_connection_revision": "mock-connection-v1",
        "idempotency_key": "mock-provider-test",
        "episode_ids": (EPISODE_ONE_ID,),
    }
    values.update(changes)
    return MediaCommand(**values)


def test_mock_media_provider_passes_reusable_contract() -> None:
    provider = MockMediaProvider(seed())

    assert_media_provider_contract(
        provider,
        MediaProviderContractContext(
            connection_id=CONNECTION_ID,
            profile_id=PROFILE_ID,
            provider_owner_user_id=OWNER_ID,
            episodes=EPISODES,
        ),
    )

    assert provider.health(CONNECTION_ID).simulated


def test_mock_rejects_unknown_profiles_episodes_and_connections() -> None:
    provider = MockMediaProvider(seed())

    with pytest.raises(
        PermissionError,
        match=MediaProviderErrorMessages.PROFILE_NOT_AUTHORIZED,
    ):
        provider.execute(command(profile_id=OTHER_PROFILE_ID))
    with pytest.raises(
        ValueError,
        match=MediaProviderErrorMessages.EPISODE_NOT_FOUND,
    ):
        provider.execute(command(episode_ids=(UUID(int=999),)))
    with pytest.raises(
        ValueError,
        match=MediaProviderErrorMessages.CONNECTION_NOT_FOUND,
    ):
        provider.health(UUID(int=999))


def test_mock_models_failure_latency_unavailability_and_stale_state() -> None:
    delays = []
    failure_configuration = replace(
        DEFAULT_MOCK_MEDIA_PROVIDER_CONFIGURATION,
        latency_seconds=0.25,
        failing_commands=frozenset({MediaCommandKind.MARK_WATCHED}),
    )
    provider = MockMediaProvider(seed(), failure_configuration, delays.append)

    with pytest.raises(
        RuntimeError,
        match=MediaProviderErrorMessages.COMMAND_FAILED,
    ):
        provider.execute(command())
    assert delays == [0.25]

    stale_provider = MockMediaProvider(
        seed(),
        replace(DEFAULT_MOCK_MEDIA_PROVIDER_CONFIGURATION, stale_writes=True),
    )
    action = command()
    result = stale_provider.execute(action)
    assert not stale_provider.verify(action, result)

    stale_provider.set_available(False)
    assert not stale_provider.health(CONNECTION_ID).available
    with pytest.raises(
        ConnectionError,
        match=MediaProviderErrorMessages.PROVIDER_UNAVAILABLE,
    ):
        stale_provider.connection_revision(CONNECTION_ID)


def test_mock_detects_revision_drift_and_idempotency_collisions() -> None:
    provider = MockMediaProvider(seed())
    original = command()
    provider.execute(original)

    with pytest.raises(
        ValueError,
        match=MediaProviderErrorMessages.IDEMPOTENCY_KEY_REUSED,
    ):
        provider.execute(command(command_id=UUID(int=711)))

    provider.advance_connection_revision("mock-connection-v2")
    assert provider.connection_revision(CONNECTION_ID) == "mock-connection-v2"


def test_mock_rejects_seed_state_referencing_unknown_episode() -> None:
    invalid_seed = MockMediaProviderSeed(
        connection_id=CONNECTION_ID,
        episodes=EPISODES,
        profiles=(
            MockMediaProviderProfileSeed(
                profile_id=PROFILE_ID,
                watched_episode_ids=frozenset({UUID(int=999)}),
            ),
        ),
    )

    with pytest.raises(
        ValueError,
        match=MediaProviderErrorMessages.MOCK_SEED_INVALID,
    ):
        MockMediaProvider(invalid_seed)
