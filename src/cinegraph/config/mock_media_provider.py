from dataclasses import dataclass

from cinegraph.common.error_messages import MediaProviderErrorMessages
from cinegraph.domain.enums.enum import MediaCommandKind


@dataclass(frozen=True, slots=True)
class MockMediaProviderConfiguration:
    provider_label: str
    connection_revision: str
    external_reference_prefix: str
    state_revision_prefix: str
    latency_seconds: float
    unavailable: bool
    failing_commands: frozenset[MediaCommandKind]
    stale_writes: bool
    fail_verification: bool

    def __post_init__(self) -> None:
        text_values = (
            self.provider_label,
            self.connection_revision,
            self.external_reference_prefix,
            self.state_revision_prefix,
        )
        if any(not value or value.strip() != value for value in text_values):
            raise ValueError(MediaProviderErrorMessages.MOCK_CONFIGURATION_INVALID)
        if self.latency_seconds < 0:
            raise ValueError(MediaProviderErrorMessages.MOCK_CONFIGURATION_INVALID)
        if not isinstance(self.failing_commands, frozenset) or not all(
            isinstance(value, MediaCommandKind) for value in self.failing_commands
        ):
            raise ValueError(MediaProviderErrorMessages.MOCK_CONFIGURATION_INVALID)


DEFAULT_MOCK_MEDIA_PROVIDER_CONFIGURATION = MockMediaProviderConfiguration(
    provider_label="CineGraph mock provider (simulated; no media is controlled)",
    connection_revision="mock-connection-v1",
    external_reference_prefix="mock-action-",
    state_revision_prefix="mock-state-v",
    latency_seconds=0.0,
    unavailable=False,
    failing_commands=frozenset(),
    stale_writes=False,
    fail_verification=False,
)
