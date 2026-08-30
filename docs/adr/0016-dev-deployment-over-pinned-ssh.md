# ADR-0016: Activation-gated Dev promotion over pinned SSH

- Status: accepted for implementation
- Date: 2026-08-30

## Context

Phase 42/43 produces an attested immutable GHCR image, but the Hostinger VPS is not
yet reachable from the project workstation. Dev promotion needs to be prepared
without turning missing access or missing credentials into an accidental deployment,
and without introducing trust-on-first-use SSH behavior.

## Decision

Add a Dev-only GitHub Actions workflow that runs after a successful first-party
`Publish immutable image` workflow run on `main`. It resolves the exact image digest,
verifies the GitHub OIDC attestation for the exact source SHA/ref and signer workflow,
rejects self-hosted-runner attestations, and then sends only the release SHA, digest,
and fixed remote deployment script over SSH.

The job is guarded by the repository-level variable
`CINEGRAPH_DEV_DEPLOY_ENABLED == "true"`. Missing or any other value skips the job
before the `dev` Environment is entered and before SSH is attempted. When enabled,
the `dev` GitHub Environment supplies the host/user variables and mode-0600 SSH key
and exact known-hosts content. Strict host-key checking and `IdentitiesOnly` are
mandatory; `ssh-keyscan` is forbidden.

The remote script requires Linux `x86_64`, an existing operator-managed Dev env file,
Docker Compose, and Git. It checks out the public repository at the exact release SHA,
creates a candidate environment changing only image digest/release SHA, validates
Compose, pulls by digest, runs the explicit migration and Qdrant provisioning jobs,
then atomically installs the candidate env and release pointer before starting and
health-checking the app. It never rebuilds, deletes volumes, runs `compose down`, or
transfers secrets/corpus files. It does not automatically downgrade a database after
a migration; recovery remains an exact-digest operator procedure.

Prod automation, GitHub Environment configuration, VPS mutation, TLS/DNS, backups,
and private corpus/API-key transfer remain outside this phase.

## Consequences

The workflow is inert until the operator creates and protects the `dev` Environment,
adds the exact SSH trust material, verifies Hostinger architecture, and flips the
repository activation variable last. Missing activation is an explicit skipped
deployment and does not fail protected `main` or image publication. A remote failure
can be retried from the same completed workflow run after remediation; a failed
migration is not auto-rolled back.
