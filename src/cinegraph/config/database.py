from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class DatabaseConfiguration:
    development_path: Path
    sqlite_driver_name: str
    sqlite_backend_name: str
    postgresql_driver_name: str
    pool_size: int
    max_overflow: int
    pool_timeout_seconds: int
    pool_recycle_seconds: int

    @property
    def supported_driver_names(self) -> frozenset[str]:
        return frozenset(
            {
                self.sqlite_driver_name,
                self.postgresql_driver_name,
            }
        )

    def sqlite_url(self, path: Path) -> str:
        return f"{self.sqlite_driver_name}:///{path.expanduser().as_posix()}"


DEFAULT_DATABASE_CONFIGURATION = DatabaseConfiguration(
    development_path=Path("knowledge/cinegraph-development.sqlite3"),
    sqlite_driver_name="sqlite+pysqlite",
    sqlite_backend_name="sqlite",
    postgresql_driver_name="postgresql+psycopg",
    pool_size=5,
    max_overflow=10,
    pool_timeout_seconds=30,
    pool_recycle_seconds=1_800,
)
