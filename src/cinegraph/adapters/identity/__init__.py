from cinegraph.adapters.identity.in_memory_identity_repositories import (
    InMemoryIdentityUnitOfWorkFactory,
)
from cinegraph.adapters.identity.scrypt_password_hasher import ScryptPasswordHasher
from cinegraph.adapters.identity.secure_session_token_generator import (
    SecureSessionTokenGenerator,
)
from cinegraph.adapters.identity.sqlalchemy_identity_repositories import (
    SqlAlchemyIdentityUnitOfWorkFactory,
)
from cinegraph.adapters.persistence.database import create_database_engine

__all__ = [
    "ScryptPasswordHasher",
    "SecureSessionTokenGenerator",
    "InMemoryIdentityUnitOfWorkFactory",
    "SqlAlchemyIdentityUnitOfWorkFactory",
    "create_database_engine",
]

# Compatibility for already deployed identity composition roots.
create_identity_engine = create_database_engine
