# ADR-0014: Layered security and quality gates

- Status: accepted for implementation
- Date: 2026-08-29

## Context

Cinegraph is a public repository containing a production-oriented application and
private, ignored corpus data. CI needs deterministic security checks without leaking
secrets or depending on optional paid/external services.

## Decision

Run Bandit against production Python sources, pip-audit against the locked production
dependency export, and CodeQL Python analysis on PRs, main pushes, and a weekly schedule.
Keep permissions least-privilege and never expose application secrets to analysis jobs.
Prepare SonarQube Cloud metadata and a conservative CodeRabbit configuration, but keep
both advisory and externally activated. Do not invent Sonar identifiers or require a
missing token.

## Consequences

Native gates are reproducible and can become required branch-protection contexts. The
dependency audit needs network access only in its dedicated job. CodeQL, CodeRabbit,
and Sonar availability/quotas are external activation boundaries and are not assumed
to be free or authoritative forever. Private corpus paths remain excluded and ignored.
