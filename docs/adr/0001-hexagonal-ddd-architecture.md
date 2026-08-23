# ADR-0001: Hexagonal and domain-driven architecture

- Status: Accepted
- Date: 2026-08-23

## Context

Cinegraph combines spoiler policy, corpus authorization, retrieval, media providers,
and external metadata. These concerns must remain testable without provider services.

## Decision

Keep business rules in `domain`, use application services for use-case orchestration,
define provider contracts in `ports`, and implement integrations in `adapters`.
Bootstrap is the composition boundary. The architecture test enforces the inward
dependency direction for domain and application modules.

## Consequences

Provider replacements and deterministic tests remain possible. New integrations require
a port and explicit composition wiring rather than imports from application code.
