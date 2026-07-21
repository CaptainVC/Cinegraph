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
- typed source provenance for episode summary data;
- a MediaWiki episode-summary provider with revision and attribution metadata;
- ports, in-memory adapters, focused unit tests, and centralized identifiers.

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

## Privacy And Corpus Policy

Private subtitle files, review ledgers, source documents, generated transcript
segments, API keys, and provider credentials are excluded from Git.

The repository contains application code and tests only. A private corpus may be
used locally through source versions and content hashes, but it is not published or
required for the test suite.

## Status

Foundation work is in progress. Retrieval evaluation, Qdrant indexing, grounded
answering, LangGraph verification, LangChain tool orchestration, authentication,
and provider actions are planned after the deterministic ingestion and source
lifecycle layers are complete.
