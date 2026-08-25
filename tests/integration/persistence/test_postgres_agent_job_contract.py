import os
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest
from sqlalchemy import create_engine, text
from tests.unit.application.agent_jobs.test_agent_job_control import _command, _job

from cinegraph.adapters.persistence.migration_runner import upgrade_database
from cinegraph.adapters.persistence.sqlalchemy_agent_job_repository import (
    SqlAlchemyAgentJobRepository,
)
from cinegraph.application.models.series_agent_result import SeriesAgentResult
from cinegraph.config import CinegraphRuntimeSettings


def test_postgres_agent_job_same_key_owner_claim_contract() -> None:
    url = os.environ.get("CINEGRAPH_TEST_DATABASE_URL")
    if not url:
        pytest.skip("CINEGRAPH_TEST_DATABASE_URL is not configured")
    settings = CinegraphRuntimeSettings(_env_file=None, database_url=url, qdrant_mode="local")
    upgrade_database(settings)
    engine = create_engine(url)
    repository = SqlAlchemyAgentJobRepository(engine)
    first = _job(_command())
    try:
        stored, created = repository.create(first)
        assert created and stored.job_id == first.job_id
        duplicate, duplicate_created = repository.create(first)
        assert not duplicate_created and duplicate.job_id == first.job_id
        assert repository.get(first.job_id, first.owner_profile_id) is not None
        assert repository.get(first.job_id, _command().owner_profile_id) is None
        barrier = Barrier(4)

        def claim():
            barrier.wait()
            return repository.claim_with_event(first.job_id)

        with ThreadPoolExecutor(max_workers=4) as pool:
            claims = tuple(pool.map(lambda _: claim(), range(4)))
        assert sum(item is not None for item in claims) == 1
        assert (
            repository.complete_with_event(first.job_id, SeriesAgentResult(None, True)) is not None
        )
        assert [event.sequence for event in repository.list_events_after(first.job_id)] == [
            1,
            2,
            3,
        ]
        with engine.begin() as connection:
            connection.execute(
                text("DELETE FROM agent_job_events WHERE job_id=:id"), {"id": str(first.job_id)}
            )
            connection.execute(
                text("DELETE FROM agent_jobs WHERE job_id=:id"), {"id": str(first.job_id)}
            )
    finally:
        engine.dispose()
