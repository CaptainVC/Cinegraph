# Corpus inventory and governed jobs

Inventory is deterministic and read-only. Human-readable output contains aggregate counts only:

```powershell
uv run python scripts/inventory_corpus.py --corpus-root knowledge
```

Modern Family S1 currently reports 24 `reviewed_ready` episodes and S2 reports 24 `awaiting_automated_review` episodes. The detail option writes only identifiers, statuses, reason codes, relative locators and hashes beneath the supplied corpus root; it rejects paths outside that root.

Planning is dry-run by default. It maps reviewed-ready episodes to transcript ingestion, script-aligned episodes to speaker review, and raw-only episodes to subtitle alignment. Missing and invalid artifacts are not auto-enqueued:

```powershell
uv run python scripts/plan_ingestion_jobs.py --corpus-root knowledge --pipeline-revision speaker-review-v1
uv run python scripts/plan_ingestion_jobs.py --corpus-root knowledge --pipeline-revision speaker-review-v1 --enqueue
```

The explicit enqueue command requires an already migrated database configured through `CINEGRAPH_DATABASE_URL` or `.env`; it never runs migrations or workers.
