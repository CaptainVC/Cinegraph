from sqlalchemy.orm import DeclarativeBase


class PersistenceBase(DeclarativeBase):
    """Single metadata registry used by every relational adapter."""
