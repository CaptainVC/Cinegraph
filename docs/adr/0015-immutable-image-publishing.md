# ADR-0015: Immutable GHCR image publishing and digest promotion

- Status: accepted for implementation
- Date: 2026-08-30

## Context

The VPS runtime must consume the exact artifact that passed the repository quality
checks. A mutable branch or `latest` tag makes rollback and provenance ambiguous,
while a source checkout on the host duplicates the build and supply-chain surface.

## Decision

After a successful first-party `Quality` push workflow on `main`, GitHub Actions
publishes exactly one GHCR tag,
`ghcr.io/captainvc/cinegraph:sha-<40-character-commit-sha>`. The workflow checks the
registry before building and fails if
the tag exists or if the registry cannot prove it is absent; it never overwrites a
release tag. The release SHA is taken from the completed workflow run, checked out
explicitly, and included in OCI source, revision, and version labels.

BuildKit emits an SBOM and maximal provenance. GitHub's OIDC-backed attestation is
attached to the resulting image digest. The workflow uses only `GITHUB_TOKEN`, with
contents read, package write, OIDC identity, and attestation permissions. The image
currently targets `linux/amd64` until the Hostinger architecture is verified.

Compose consumes the image by exact `CINEGRAPH_IMAGE@CINEGRAPH_IMAGE_DIGEST`, while
`CINEGRAPH_RELEASE_SHA` records the corresponding source commit. The validator rejects
other registries, tags, malformed digests, and malformed release SHAs. Dev must be
promoted and smoke-tested before the same digest is selected in Prod.

## Consequences

An image tag and its digest are separate: the tag identifies the release commit, and
the digest identifies the exact pushed manifest. Operators record and deploy the
digest, never a floating tag. A failed preexistence check, partial push, or failed
attestation stops the release for investigation; rerunning cannot mutate an existing
tag. GHCR package visibility and first-time package permissions remain activation
work, and no deployment or private corpus transfer is performed by this workflow.
