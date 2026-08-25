from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ObservabilityConfiguration:
    """Boundaries for privacy-safe telemetry, kept independent of logging adapters."""

    maximum_duration_ms: float = 86_400_000.0
    maximum_attribute_count: int = 16
    maximum_opaque_id_length: int = 128

    def __post_init__(self) -> None:
        if self.maximum_duration_ms <= 0:
            raise ValueError("maximum_duration_ms must be positive")
        if self.maximum_attribute_count <= 0:
            raise ValueError("maximum_attribute_count must be positive")
        if self.maximum_opaque_id_length < 16:
            raise ValueError("maximum_opaque_id_length is too small")


DEFAULT_OBSERVABILITY_CONFIGURATION = ObservabilityConfiguration()
