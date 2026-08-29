# CineGraph

CineGraph is a self-hosted, spoiler-aware intelligence layer for episodic media.
It is being built as a production-oriented modular monolith: deterministic domain
rules and ingestion first, then retrieval, grounded answers, agentic workflows,
and optional media-provider actions.

## Current Foundation

This branch establishes the first production boundaries:

- immutable watch state, manual watched/unwatched commands, and idempotent events;
- spoiler visibility as deterministic domain policy;
- source documents, immutable source versions, review metadata, and content hashes;
- reviewed subtitle promotion, canonical SRT transcript ingestion, timestamps, and
	deterministic segment identities;
- resumable OpenAI Batch speaker review with independent low-cost passes,
  higher-capability adjudication, a hard run-cost ceiling, immutable evidence,
  and truthful `automated_reviewed` provenance;
- typed source provenance for episode summary data;
- a MediaWiki episode-summary provider with revision and attribution metadata;
- spoiler-safe lexical and hybrid retrieval, grounded-answer citation verification,
  and LangGraph/LangChain orchestration boundaries;
- fail-closed corpus entitlements that restrict guest access to Modern Family
  seasons 1 and 2 independently of spoiler/watch-progress policy;
- bounded owner-scoped agent jobs at `/api/v1/agent/jobs` with a durable SQL job/event
  store, atomic lifecycle transitions, and cursor-correct replayable SSE; the bounded
  dispatcher and LangGraph checkpoint remain process-local pending worker recovery
  supervision. See `docs/agent-jobs-api.md` for the request and reconnect contract.
- content-free structured runtime telemetry, correlated request/job lifecycle events,
  classified transient retries, cooperative deadlines, and strict cross-model token/
  estimated-cost budgets for the series research agent;
- ports, in-memory adapters, focused unit tests, and centralized identifiers.
- a same-origin guest/auth web experience for spoiler-scoped, citation-backed chat.
- an evidence-backed recommendation workflow that ranks only deterministically
  entitled and spoiler-visible candidates.
- provider-neutral media commands with defense-in-depth authorization, exact-parameter
  approvals, resumable LangGraph interrupts, idempotency, verification, and audit.
- a deterministic, clearly labeled mock media provider with synthetic profile state
  and a reusable adapter contract for future real providers.
- a hardened Jellyfin HTTP adapter with reviewed item mappings, redacted credentials,
  bounded retries/circuit breaking, idempotency, and read-after-write verification.
- authenticated, review-first Netflix viewing-history CSV reconciliation with strict
  upload validation, deterministic candidates, retention, and idempotent watch events.
- an authorization-safe series research runtime with bounded transcript and
  GraphRAG tools, structured current-turn citation verification, and typed safe
  refusals.

## Architecture

```text
domain       Business entities, value objects, policies, and invariants
application  Use cases that orchestrate domain behavior through ports
ports        Protocols for external capabilities and persistence
adapters     In-memory, filesystem, HTTP, and future database implementations
ingestion    Deterministic subtitle parsing and canonicalization pipelines
```

The dependency direction is intentional:

```text
application -> domain + ports <- adapters
```

The domain does not depend on FastAPI, Postgres, Qdrant, LangChain, LangGraph, or
provider SDKs.

## Local Setup

Requires Python 3.12 or later and [uv](https://docs.astral.sh/uv/).

```zsh
uv sync
uv run pytest
uv build --wheel
```

Identity persistence uses SQLAlchemy behind an explicit unit of work. Apply the
checked-in Alembic schema before using authentication against a fresh database:

```zsh
uv run python scripts/migrate_database.py upgrade
```

Development defaults to a gitignored SQLite URL. Production fails closed unless
`CINEGRAPH_DATABASE_URL` parses to a `postgresql+psycopg://` URL. The API never calls
`create_all` or silently migrates at startup; see `docs/database.md` for lifecycle,
pool, and downgrade guidance.

## Privacy And Corpus Policy

Private subtitle files, review ledgers, source documents, generated transcript
segments, API keys, and provider credentials are excluded from Git.

The repository contains application code, tests, and the non-sensitive
`knowledge/catalogue.json` manifest. Subtitle files, ledgers, scripts, PDFs, generated
metadata and derived artifacts remain ignored; local inventory reports only aggregate
readiness unless a caller explicitly requests a safe detail file beneath the corpus root.

## Private Speaker Review

The private corpus remains outside Git. The review workflow reads screenplay PDFs
and script-aligned SRT files from a caller-provided corpus directory, writes all
run artifacts beneath that directory, and never modifies the source files.

Model names, thresholds, pricing assumptions, the Batch endpoint, output schema,
file patterns, and the cost limit are centralized under `src/cinegraph/config`.
The default policy runs two independent `gpt-5.6-luna` opinions and sends only
disagreements or low-confidence cases to `gpt-5.6-terra`. A completed file is
recorded as `automated_reviewed`; it is not represented as human-reviewed.
Cases still unresolved after Terra may enter one final conservative
`gpt-5.6-sol` graph stage with a separately configured confidence threshold and
the same evidence allowlist. Anything that still fails remains human review;
thresholds are never lowered merely to eliminate the queue.
Final-review responses that fail to produce valid structured output are retried
once with a larger centralized output allowance. The retry targets only missing
verdicts, remains inside the run budget, records all consumed tokens (including
malformed responses), and never re-asks cases where Sol explicitly requested a
human decision.

Corpus-review lifecycle transitions are compiled as a LangGraph workflow. The
graph owns prepare/load, submission, and resumable advancement routing, while the
underlying application workflow retains deterministic evidence validation,
consensus, budget enforcement, immutable artifacts, and promotion policy. This
keeps future corpora easy to start or resume without granting an LLM authority
over source governance.

Provision an environment file from a temporary labelled key file. This command
copies only `OPENAI_API_KEY`, excludes Moonshot credentials, creates the destination
with private permissions, and can delete the temporary server-side source:

```zsh
uv run python scripts/provision_openai_env.py /secure/staging/key.txt .env --delete-source
```

Prepare and submit a resumable review for guest-visible seasons 1 and 2:

```zsh
uv run python scripts/review_speakers_with_openai.py run \
  --corpus-root knowledge --seasons 1 2 --wait
```

Without `--wait`, the command submits the primary Batch and returns immediately.
Use the emitted run directory to inspect or advance it later:

```zsh
uv run python scripts/review_speakers_with_openai.py submit knowledge/review-runs/<run-id>
uv run python scripts/review_speakers_with_openai.py status knowledge/review-runs/<run-id>
uv run python scripts/review_speakers_with_openai.py advance knowledge/review-runs/<run-id>
uv run python scripts/review_speakers_with_openai.py final-review knowledge/review-runs/<run-id>
uv run python scripts/review_speakers_with_openai.py retry-incomplete knowledge/review-runs/<run-id>
uv run python scripts/review_speakers_with_openai.py reconcile-costs knowledge/review-runs/<run-id>
uv run python scripts/review_speakers_with_openai.py wait knowledge/review-runs/<run-id>
```

No output is promoted while any item still needs review. Residual cases are written
to `human-review-queue.json`; successful runs produce cleaned SRT files, an
immutable decision ledger, token/cost records, source hashes, and a deterministic
calibration sample.

### Private human resolution

When the conservative agent stages still leave genuine ambiguity, generate the
self-contained offline workbench inside the ignored run directory:

```bash
uv run python scripts/review_speakers_human.py prepare knowledge/review-runs/<run-id>
```

The HTML workbench makes no network requests, stores resumable progress in the
browser when available, restricts choices to the candidate allowlist, and exports
`human-review-resolution.json`. Apply that file with:

```bash
uv run python scripts/review_speakers_human.py apply knowledge/review-runs/<run-id> /path/to/human-review-resolution.json
```

Resolution validation is all-or-nothing. It validates the run ID, exact queue hash,
schema, reviewer identity, timezone-aware timestamp, one decision per queued
candidate, allowlisted speaker, and a human rationale. Prior agent artifacts remain immutable.
The resulting SRTs and ledger use truthful `hybrid_reviewed` provenance and become
eligible for canonical ingestion and indexing only after every queued case resolves.

## Corpus access boundary

Corpus entitlement and spoiler visibility are separate, cumulative restrictions.
The default guest scope is centrally configured for the canonical Modern Family
series identifier and seasons 1–2 only. Authenticated scopes may grant additional
seasons or corpora, but must be constructed by the trusted application boundary;
model-visible tool arguments cannot supply or widen them.

Episode summaries, transcript readers, season search, hybrid Qdrant scopes,
conversation-thread bindings, and LangGraph runtime context all carry the immutable
scope. Disallowed requests return no evidence, and Qdrant results are revalidated
against the exact compiled episode and timestamp boundary before becoming model
context.

## Security

See [SECURITY.md](SECURITY.md) for vulnerability reporting and repository security
guidelines. Do not commit private corpus content, review ledgers, provider tokens,
or local environment files.

## Status

Foundation work is in progress. Governed retrieval, persistent application
composition, corpus evaluation, authentication, HTTP contracts, guardrails, and
the first product UI are present. Provider actions and deployment hardening remain
ahead.

## Development quality contract

Prerequisites: Python 3.12 or newer, `uv`, and Git. The commands below are
cross-platform (PowerShell, cmd, Bash, and zsh) and use the committed lock file:

```text
uv sync --locked --dev
uv run python scripts/quality.py
```

The quality runner fails at the first unsuccessful stage and runs Ruff, the staged
mypy boundary (`domain`, `ports`, `config`, identity and persistence adapters, and
application models/policy/serialization/services), full tests with branch coverage,
deterministic synthetic retrieval evaluation, pre-commit, and a wheel build.
Individual checks remain available:

```text
uv run ruff check .
uv run mypy
uv run pytest --cov --cov-report=term-missing --cov-report=xml --cov-report=json
uv run python scripts/run_synthetic_evaluation.py
uv run pre-commit run --all-files
uv build --wheel
```

The verified branch baseline is 87.04% total branch coverage (874 passed and 5
skipped, measured with `pytest-cov` on 2026-08-29). The centralized coverage
configuration floors ordinary (non-browser) tests at 87%, so coverage cannot
silently regress. Coverage XML and JSON reports are generated locally and uploaded
by CI. Ruff syntax/error classes, Pyflakes, and import sorting are gated across the
repository. Formatter enforcement remains intentionally staged to avoid mixing a
repository-wide style rewrite with behavioral phases.

Browser end-to-end tests are marked `e2e` and excluded from ordinary test and coverage
runs. Install the locked development dependencies and Chromium, then run them with:

```text
uv sync --locked --dev
uv run playwright install chromium
uv run pytest -o addopts='' tests/e2e -m e2e --no-cov
```

The dedicated CI job installs Chromium with system dependencies before running this
same marker-scoped command. It uploads synthetic-only screenshots and Playwright
traces for failed cases; those artifacts never contain private corpus data or
credentials.

Architecture boundaries and phase workflow are recorded in [AGENTS.md](AGENTS.md),
with decisions indexed in [docs/adr/README.md](docs/adr/README.md).
