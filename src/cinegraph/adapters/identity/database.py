"""Compatibility import for identity adapters.

The shared persistence package owns the engine implementation now.
"""

from cinegraph.adapters.persistence.database import create_database_engine

create_identity_engine = create_database_engine

__all__ = ["create_identity_engine"]
