# ADR-0002: Privacy and governance boundary

- Status: Accepted
- Date: 2026-08-23

## Context

Subtitle corpora, provider responses, and user watch state can be private or rights-
restricted. Retrieval also needs spoiler and entitlement guarantees.

## Decision

Private corpora and secrets remain outside Git and CI. Trusted application boundaries
construct immutable corpus scopes and spoiler state before retrieval; model-visible
arguments cannot widen them. Media writes require explicit human approval, deterministic
IDs/versioning, and audit records.

## Consequences

CI uses only invented fixtures. Operators must stage private inputs locally, and every
future provider/action integration must preserve authorization and audit evidence.
