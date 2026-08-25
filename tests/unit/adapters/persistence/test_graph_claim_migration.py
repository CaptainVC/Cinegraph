from pathlib import Path

from sqlalchemy import create_engine, inspect, text

from cinegraph.adapters.persistence.migration_runner import (
    downgrade_database,
    upgrade_database,
)
from cinegraph.config import CinegraphRuntimeSettings


def _settings(path: Path) -> CinegraphRuntimeSettings:
    return CinegraphRuntimeSettings(_env_file=None, identity_database_path=path)


def _kind_check_sql(path: Path) -> str:
    engine = create_engine(f"sqlite+pysqlite:///{path.as_posix()}")
    try:
        constraints = inspect(engine).get_check_constraints("ingestion_jobs")
        return next(
            str(item["sqltext"])
            for item in constraints
            if item["name"] == "ck_ingestion_jobs_kind_allowed"
        )
    finally:
        engine.dispose()


def test_graph_claim_migration_roundtrips_and_reverts_ingestion_kind(tmp_path: Path) -> None:
    database_path = tmp_path / "graph-claims.sqlite3"
    settings = _settings(database_path)

    upgrade_database(settings)
    engine = create_engine(f"sqlite+pysqlite:///{database_path.as_posix()}")
    try:
        table_names = set(inspect(engine).get_table_names())
        assert {
            "graph_entities",
            "graph_entity_aliases",
            "graph_claims",
            "graph_claim_evidence",
        } <= table_names
        with engine.connect() as connection:
            assert connection.execute(
                text("SELECT version_num FROM alembic_version")
            ).scalar_one() == ("0004_agent_jobs")
    finally:
        engine.dispose()
    assert "graph_claim_extraction" in _kind_check_sql(database_path)

    downgrade_database(settings, "0002_ingestion_jobs")
    engine = create_engine(f"sqlite+pysqlite:///{database_path.as_posix()}")
    try:
        assert "graph_claims" not in inspect(engine).get_table_names()
        with engine.connect() as connection:
            assert connection.execute(
                text("SELECT version_num FROM alembic_version")
            ).scalar_one() == ("0002_ingestion_jobs")
    finally:
        engine.dispose()
    assert "graph_claim_extraction" not in _kind_check_sql(database_path)

    upgrade_database(settings)
    engine = create_engine(f"sqlite+pysqlite:///{database_path.as_posix()}")
    try:
        assert {"graph_claims", "agent_jobs"} <= set(inspect(engine).get_table_names())
    finally:
        engine.dispose()
