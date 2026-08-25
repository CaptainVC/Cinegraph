from pathlib import Path

from sqlalchemy import create_engine, inspect, text

from cinegraph.adapters.persistence.migration_runner import downgrade_database, upgrade_database
from cinegraph.config import CinegraphRuntimeSettings


def test_agent_jobs_migration_upgrade_downgrade_reupgrade(tmp_path: Path) -> None:
    path = tmp_path / "migrations.sqlite3"
    settings = CinegraphRuntimeSettings(_env_file=None, identity_database_path=path)
    upgrade_database(settings)
    engine = create_engine(f"sqlite:///{path}")
    try:
        inspector = inspect(engine)
        assert {"agent_jobs", "agent_job_events"} <= set(inspector.get_table_names())
        assert inspector.get_foreign_keys("agent_job_events")[0]["options"]["ondelete"] == "CASCADE"
        assert {item["name"] for item in inspector.get_unique_constraints("agent_jobs")} >= {
            "uq_agent_jobs_owner_key"
        }
        assert {item["name"] for item in inspector.get_unique_constraints("agent_job_events")} >= {
            "uq_agent_job_events_sequence"
        }
        assert "ck_agent_jobs_error_code_allowed" in {
            item["name"] for item in inspector.get_check_constraints("agent_jobs")
        }
        with engine.connect() as connection:
            assert (
                connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
                == "0004_agent_jobs"
            )
    finally:
        engine.dispose()
    downgrade_database(settings, "0003_graph_claims")
    engine = create_engine(f"sqlite:///{path}")
    try:
        assert "agent_jobs" not in inspect(engine).get_table_names()
    finally:
        engine.dispose()
    upgrade_database(settings)
    engine = create_engine(f"sqlite:///{path}")
    try:
        assert {"agent_jobs", "agent_job_events"} <= set(inspect(engine).get_table_names())
    finally:
        engine.dispose()
