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
  store, atomic lifecycle transitions, cursor-correct replayable SSE, and startup
  recovery under one supervised API process; the bounded dispatcher and LangGraph
  checkpoint remain process-local. See `docs/agent-jobs-api.md` for the request,
  recovery, and reconnect contracts.
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

### VPS container baseline

Phase 40 includes a pinned, non-root Docker image and an isolated Compose stack for
one Dev and one Prod environment. PostgreSQL and Qdrant stay on an internal Docker
network; only the API's configurable loopback port is published. Copy
`deploy/env/dev.env.example` or `deploy/env/prod.env.example` to a mode-0600 file
outside Git, replace all placeholders, and validate it before starting the stack:

```bash
python3 scripts/validate_vps_runtime.py --environment production \
  --env-file /etc/cinegraph/prod.env --compose-file deploy/compose.yaml
docker compose --env-file /etc/cinegraph/prod.env -f deploy/compose.yaml up -d postgres qdrant
docker compose --env-file /etc/cinegraph/prod.env -f deploy/compose.yaml pull app
docker compose --env-file /etc/cinegraph/prod.env -f deploy/compose.yaml --profile migration run --rm migrate
docker compose --env-file /etc/cinegraph/prod.env -f deploy/compose.yaml --profile provisioning \
  run --rm provision-qdrant
docker compose --env-file /etc/cinegraph/prod.env -f deploy/compose.yaml up -d app
```

The API does not auto-migrate or auto-provision Qdrant. See
`docs/operations/vps-host-baseline.md` for host layout, backup, rollback, and the
explicit Hostinger SSH activation boundary.

Published releases are immutable GHCR images tagged by the reviewed commit and
deployed by exact digest. The `Publish immutable image` workflow runs only after a
successful main-branch quality run, emits SBOM/provenance, and attaches a GitHub
attestation; it does not deploy or receive corpus/API-key data. Verify and promote
the exact digest through Dev before Prod. See
[`docs/operations/image-release.md`](docs/operations/image-release.md).

The Dev promotion workflow is activation-gated: it skips unless the repository
variable `CINEGRAPH_DEV_DEPLOY_ENABLED` is exactly `true`, then requires the protected
`dev` Environment, pinned SSH host key, and operator-approved credentials. It is
currently an activation-ready contract only; it does not configure GitHub, mutate
the VPS, transfer private data, or automate Prod.

Phase 45 adds the operator-run Hostinger console bootstrap. It installs a root-owned
forced-command dispatcher and no-argument privileged helper while keeping the
password-disabled SSH account outside Docker/admin groups and keeping `/etc/cinegraph/dev.env`
root:root mode `0600`. Run bootstrap apply/check from a reviewed checkout, compare
its public server-key fingerprint independently, configure the protected GitHub
Environment, and enable the repository activation variable last. See the
[image release runbook](docs/operations/image-release.md).

## Privacy And Corpus Policy

Private subtitle files, review ledgers, source documents, generated transcript
segments, API keys, and provider credentials are excluded from Git.

The repository contains application code, tests, and the non-sensitive
`knowledge/catalogue.json` manifest. Subtitle files, ledgers, scripts, PDFs, generated
metadata and derived artifacts remain ignored; local inventory reports only aggregate
readiness unless a caller explicitly requests a safe detail file beneath the corpus root.

## Private Speaker Review

Private corpus handoff is constrained by the [Phase 51 private-corpus bundle
boundary](docs/operations/private-corpus-transfer.md). Bundles are explicit,
deterministic, and fail closed; the bundle tools never transfer a whole corpus root
or mutate a live volume. Phase 52 adds a distinct, operator-run, pinned-SSH transfer
identity that publishes verified bundles as immutable root-private Dev objects. It
does not reuse the deployment key, pass corpus metadata in the SSH command, or mutate
the application, database, vector store, named volumes, or guest scope. See
[ADR-0018](docs/adr/0018-private-corpus-vps-handoff.md) and the transfer runbook.

Phase 55 adds a second exact forced command for validating and ingesting the already
installed reviewed Season 1 object. Bundle bytes are not retransmitted. The locked
root processor revalidates the immutable object and active catalogue, then runs a
read-only, unprivileged, backend-only Compose one-shot with no OpenAI, identity, or
PostgreSQL credential. See [ADR-0019](docs/adr/0019-private-corpus-processing-boundary.md)
and the [processing runbook](docs/operations/private-corpus-processing.md).

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

Paid Batch submissions use an intent/completion journal so an interrupted state
write can reuse the accepted submission. Ambiguous attempts require operator
reconciliation and cannot be resubmitted automatically. Creation calls disable SDK
retries; see the [submission recovery runbook](docs/operations/speaker-review-submission-recovery.md).

Every review run is confined to the canonical
`<corpus-root>/review-runs/speaker-review-<16 hex>` layout. Source manifests are
versioned and store only root-relative POSIX locators plus the exact byte length and
SHA-256 digest. Reads reject traversal, absolute or noncanonical locators,
symlinks/junctions/reparse points, hardlinked files, case-colliding source names,
oversized files, and files that change while being read. Run artifacts are written
with enforced private POSIX permissions on the Linux worker and safe create-once or
atomic-replace semantics. Windows private runs require separately administered,
exclusive NTFS ACLs and are not the supported production execution target. The
OpenAI adapter receives the same in-memory JSONL bytes that were hashed into the
submission journal, so it never reopens a mutable request path. See the
[speaker-review filesystem security runbook](docs/operations/speaker-review-filesystem-security.md).

The VPS exposes preparation through a separate offline `speaker-review-v1`
forced-command boundary. Its isolated container can validate and prepare only the
private Season 2 corpus; it has no network or OpenAI secret, and the source mount is
read-only while `review-runs` is mounted separately for confined writes. Paid Batch
submission remains a later, explicit operation. See the
[private speaker-review preparation runbook](docs/operations/private-speaker-review-preparation.md).

Phase 61 adds the separately authorized primary submission boundary. A root-owned
authorization file binds the exact prepared receipt, run, cost ceiling, and
authorization UUID. The standard-library-only root coordinator mounts only
`review-runs` read-write into the egress-only submit worker and keeps provider
credentials out of its request and process environment. Create-once root intent
and completion receipts, together with the workflow's intent/completed journals,
make retries safe: matching receipts do not start a worker, completed journals
repair state without another provider call, and unresolved intents require
operator reconciliation. See [ADR-0021](docs/adr/0021-private-speaker-review-primary-submission-boundary.md)
and the [primary-submission runbook](docs/operations/private-speaker-review-primary-submission.md).

Phase 62 separates provider observation from lifecycle advancement. The dedicated
LangGraph operation retrieves at most one active primary Batch, then either waits,
records a terminal failure, or persists that part's output at an explicit
`primary_part_completed` checkpoint. It has no route to another submission,
adjudication, finalization, or ingestion. Its egress-only Compose worker receives
only the private run mount and a secret file and emits an aggregate allowlist. See
[ADR-0022](docs/adr/0022-bounded-primary-observation-transition.md) and the
[primary-observation runbook](docs/operations/private-speaker-review-primary-observation.md).

Phase 63 exposes first-part observation through a second exact command on the
dedicated review SSH identity. A standard-library-only root coordinator binds the request
to its root authorization, preparation/submission receipts, active immutable
release and image, configuration, digest-selected run directory, and complete
pre/post artifact evidence. Pending results remain safely repeatable; completed or
failed transitions receive create-once receipts, and receipt repair never reopens
the provider boundary. See [ADR-0023](docs/adr/0023-private-speaker-review-observation-boundary.md)
and the [VPS observation runbook](docs/operations/private-speaker-review-observation.md).

Phase 64 adds a bounded next-primary LangGraph transition and isolated Compose
worker. It accepts only an explicit completed-part checkpoint, validates the
completed output and immutable submission journals before opening the secret,
and creates at most one subsequent primary Batch. It cannot observe, parse,
adjudicate, finalize, or ingest. Fully observed primary runs are a provider-free
no-op, and post-checkpoint submission replays reject the Phase 61 first-part
shape. The root VPS authorization/receipt boundary remains a separate next phase.
See [ADR-0024](docs/adr/0024-bounded-next-primary-submission-transition.md) and
the [next-primary transition runbook](docs/operations/private-speaker-review-next-primary-submission.md).

Phase 65 adds the separately authorized VPS `speaker-review-submit-next-primary-v1`
boundary. It binds the Phase 60 preparation, Phase 61 submission, and Phase 63
part-one observation receipts before submitting exactly part two. The root
intent includes the exact request digest and runtime image/configuration
bindings, and the egress-only worker cannot observe, adjudicate, finalize, or
ingest. See [ADR-0025](docs/adr/0025-private-speaker-review-next-primary-boundary.md)
and the [VPS next-primary boundary runbook](docs/operations/private-speaker-review-next-primary-boundary.md).

Phase 66 adds the fourth exact review command,
`speaker-review-observe-next-primary-v1`, for observing only submitted primary
part two. The root coordinator revalidates the complete Phase 60/61/63/65
receipt chain, active release/image/configuration, and the exact pre-state,
request, artifact, and journal digests before opening the provider boundary.
Those root-verified bytes are carried into LangGraph without a same-UID
filesystem reload, closing the observer TOCTOU window for both primary parts.
Waiting observations remain receipt-free and retryable; terminal observation
or failure receives a part-specific create-once receipt. The command cannot
submit another part, adjudicate, finalize, ingest, or select an arbitrary part.
See [ADR-0026](docs/adr/0026-private-speaker-review-next-primary-observation-boundary.md)
and the [part-two observation runbook](docs/operations/private-speaker-review-next-primary-observation.md).

Phase 67 separates provider-free primary-result interpretation from every paid
downstream action. A new LangGraph operation validates the complete observed
Luna result set, fails closed on incomplete or impossible usage/cost metadata,
and either finalizes unanimous high-confidence consensus locally or writes
deterministic Terra request parts and stops at `adjudication_prepared`. Its
isolated container has no network or OpenAI secret and requires distinct
root-computed digests for state, requests, journals, observations/API errors,
and derived evidence. See [ADR-0027](docs/adr/0027-provider-free-primary-result-processing.md)
and the [primary-result processing runbook](docs/operations/private-speaker-review-primary-result-processing.md).

Phase 68 exposes that exact transition as the fifth forced review command,
`speaker-review-process-primary-results-v1`. A root coordinator revalidates the
complete preparation and two-part submission/observation receipt chain, active
release/image/configuration, immutable source snapshot, and five independent
pre-state digest classes before launching the secretless, network-disabled
worker. The worker receives only the digest-selected source mount read-only and
its corresponding review-runs mount read-write. Create-once root intent and
completion receipts make exact replay and crash recovery auditable without
widening the command into Terra submission, promotion, or ingestion. See
[ADR-0028](docs/adr/0028-private-primary-result-processing-boundary.md) and the
[VPS processing runbook](docs/operations/private-speaker-review-primary-result-processing-boundary.md).

Phase 69 adds the sixth forced review command,
`speaker-review-submit-first-adjudication-v1`, for the first and only the first
prepared Terra adjudication part. The egress worker receives the OpenAI key as
a read-only Compose secret, rechecks the exact root-selected request digest at
the provider-call boundary, and cannot observe results, submit a later part,
enter final review, promote corpus files, or ingest data. The root coordinator
binds the complete Phase 68 receipt, the five pre-state digest classes, the
active release/image/configuration, a fresh authorization and cost ceiling, and
the deterministic total estimated adjudication cost. A completed provider
journal can be replayed without a second call; an unmatched intent instead
requires explicit reconciliation. See
[ADR-0029](docs/adr/0029-private-first-adjudication-submission-boundary.md) and
the [VPS first-adjudication submission runbook](docs/operations/private-speaker-review-first-adjudication-submission-boundary.md).

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
CLI summaries expose the non-sensitive run ID rather than an absolute private path.
Use that ID in the canonical run layout to inspect or advance it later:

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

The quality runner fails at the first unsuccessful stage and runs Ruff, Bandit SAST,
the staged mypy boundary (`domain`, `ports`, `config`, identity and persistence
adapters, and application models/policy/serialization/services), full tests with
branch coverage, deterministic synthetic retrieval evaluation, pre-commit, and a
wheel build.
Individual checks remain available:

```text
uv run ruff check .
uv run bandit --recursive src scripts --configfile pyproject.toml --severity-level medium --confidence-level medium
uv run mypy
uv run pytest --cov --cov-report=term-missing --cov-report=xml --cov-report=json
uv run python scripts/run_synthetic_evaluation.py
uv run pre-commit run --all-files
uv build --wheel
```

The verified branch baseline is 87.15% total branch coverage (1,178 passed and 14
skipped, measured with `pytest-cov` on 2026-09-04). The centralized coverage
configuration floors ordinary (non-browser) tests at 87%, so coverage cannot
silently regress. Coverage XML and JSON reports are generated locally and uploaded
by CI. Ruff syntax/error classes, Pyflakes, and import sorting are gated across the
repository. Formatter enforcement remains intentionally staged to avoid mixing a
repository-wide style rewrite with behavioral phases.

The network-backed locked-production dependency audit runs as a separate CI gate so
the ordinary local loop remains usable offline. Its reproducible command, CodeQL
policy, immutable-action enforcement, and optional SonarQube Cloud/CodeRabbit setup
are documented in [the security quality-gates runbook](docs/operations/security-quality-gates.md).

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
