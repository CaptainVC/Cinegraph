from cinegraph.adapters.persistence.migration_runner import (
    downgrade_database,
    upgrade_database,
)
from cinegraph.config import CinegraphRuntimeSettings

# Existing identity callers are kept as a narrow migration compatibility seam;
# all new code uses the domain-neutral names from adapters.persistence.
upgrade_identity_database = upgrade_database


def downgrade_identity_database(settings: CinegraphRuntimeSettings, revision: str = "-1") -> None:
    # Legacy identity tests/callers historically removed the complete identity schema.
    # The domain-neutral runner retains Alembic's normal one-revision default.
    downgrade_database(settings, "base" if revision == "-1" else revision)

__all__ = [
    "downgrade_identity_database",
    "upgrade_identity_database",
]
