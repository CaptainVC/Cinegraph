from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class SecretProvisioningConfiguration:
    key_value_pattern: str
    openai_key_name: str
    excluded_key_names: frozenset[str]
    private_file_mode: int


DEFAULT_SECRET_PROVISIONING_CONFIGURATION = SecretProvisioningConfiguration(
    key_value_pattern=(
        r"^(?P<name>[A-Za-z_][A-Za-z0-9_]*)\s*=\s*(?P<value>.*)$"
    ),
    openai_key_name="OPENAI_API_KEY",
    excluded_key_names=frozenset({"OPENAI_API_KEY", "MOONSHOT_API_KEY"}),
    private_file_mode=0o600,
)
