"""Plan (or explicitly enqueue) governed corpus jobs without executing workers."""

import argparse
from pathlib import Path

from cinegraph.adapters.catalogue import JsonCatalogueManifestLoader
from cinegraph.adapters.date_time.system_clock import SystemClock
from cinegraph.adapters.persistence.database import create_database_engine
from cinegraph.adapters.persistence.sqlalchemy_ingestion_job_repository import (
    SqlAlchemyIngestionJobUnitOfWorkFactory,
)
from cinegraph.application.service.corpus_inventory_service import CorpusInventoryService
from cinegraph.application.service.ingestion_job_planning_service import IngestionJobPlanningService
from cinegraph.application.service.ingestion_job_service import IngestionJobService
from cinegraph.config import CinegraphRuntimeSettings


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--corpus-root", type=Path, required=True)
    parser.add_argument("--catalogue", type=Path, default=Path("knowledge/catalogue.json"))
    parser.add_argument("--pipeline-revision", required=True)
    parser.add_argument("--enqueue", action="store_true")
    parser.add_argument("--env-file", type=Path, default=Path(".env"))
    args = parser.parse_args()
    loaded = JsonCatalogueManifestLoader().load(args.catalogue)
    report = CorpusInventoryService().inspect(args.corpus_root, loaded.manifest)
    if args.enqueue:
        settings = CinegraphRuntimeSettings(_env_file=args.env_file)
        engine = create_database_engine(settings)
        try:
            factory = SqlAlchemyIngestionJobUnitOfWorkFactory(engine)
            planner = IngestionJobPlanningService(IngestionJobService(factory, SystemClock()))
            plans = planner.plan(loaded.manifest, report, args.pipeline_revision, enqueue=True)
        finally:
            engine.dispose()
    else:
        plans = tuple(
            item
            for item in report.items
            if item.status.value in {"reviewed_ready", "awaiting_automated_review", "awaiting_alignment"}
        )
    print(f"planned={len(plans)} enqueued={args.enqueue}")


if __name__ == "__main__":
    main()
