# Supply-chain and image release runbook

## Release contract

The `Publish immutable image` workflow runs only after a successful first-party
`Quality` push completion on `main`. It resolves the completed commit SHA,
checks out that exact SHA, and publishes only:

```text
ghcr.io/captainvc/cinegraph:sha-<40-character-commit-sha>
```

The tag is never `latest`, a branch, or a version alias. Before building, the action
performs an authenticated GHCR manifest check. HTTP 200 means the tag already exists;
HTTP 404 is the only accepted absence; auth, network, parsing, and every other status
fail closed. This makes a rerun unable to mutate an existing release. A partial push
or failed attestation is quarantined: record the workflow run and digest, do not force
or delete the tag, and open a follow-up release only after investigating the registry
state.

The image is built only for `linux/amd64` until the Hostinger VPS architecture is
verified. Do not deploy it to an ARM host. The workflow uses the repository's
`GITHUB_TOKEN` only; no OpenAI key, VPS key, corpus file, or other secret is available
to the build. BuildKit publishes an SBOM and maximal provenance, and GitHub attaches
an OIDC-signed provenance attestation to the resulting digest.

## Verify a release

The workflow summary provides the image digest. Verify the signed attestation from a
trusted workstation with the GitHub CLI (use the syntax supported by the installed
CLI version):

```bash
gh attestation verify oci://ghcr.io/captainvc/cinegraph@sha256:<64-hex-digest> \
  --repo CaptainVC/Cinegraph \
  --signer-workflow CaptainVC/Cinegraph/.github/workflows/publish-image.yml
```

Confirm that the attestation subject digest, OCI revision label, and release SHA all
refer to the reviewed commit. The image tag is a human-readable commit locator; the
digest is the deployment identity and must be copied into the private environment
file as `CINEGRAPH_IMAGE_DIGEST=sha256:<64-hex-digest>` together with
`CINEGRAPH_RELEASE_SHA=<40-character-sha>`.

## GHCR activation gap

The package must be visible to the intended VPS pull identity. The first package push
may require repository/package visibility and organization policy activation in
GitHub. Use the minimum package permission needed by the deployment account and test
an authenticated pull on Dev. Do not put a PAT into Actions; if the VPS needs a pull
credential, provision it separately on the host with read-only package scope and
mode-0600 storage.

## Dev deployment activation

Phase 44 adds an activation-gated `Deploy Dev` workflow. It is intentionally skipped
unless the repository-level Actions variable `CINEGRAPH_DEV_DEPLOY_ENABLED` is
exactly `true`; an absent or different value cannot open an SSH connection and does
not fail `main` or image publication. The workflow is Dev-only and has no Prod job.

Before enabling it, create and protect the GitHub Environment named `dev` with a
`main` deployment branch rule and required reviewer. Add these environment-scoped
values:

```text
CINEGRAPH_DEV_HOST       (variable)
CINEGRAPH_DEV_USER       (variable)
CINEGRAPH_DEV_SSH_PRIVATE_KEY (secret)
CINEGRAPH_DEV_KNOWN_HOSTS    (secret; exact pinned host-key line)
```

The host must report `x86_64`, have Docker Compose and Git, and already contain a
mode-0600 `/etc/cinegraph/dev.env` with its OpenAI key and other operator-managed
settings. The configured SSH user must be able to acquire the deployment lock, write
`/opt/cinegraph` and `/etc/cinegraph/dev.env`, and run Docker Compose (usually via a
dedicated Docker group); do not rely on interactive `sudo`. Verify the exact
known-hosts entry independently; the workflow never uses
`ssh-keyscan`. Configure all Environment values first, test the Dev host/preflight,
and flip the repository activation variable last. This phase does not create the
Environment, mutate the VPS, or transfer secrets, SRT/PDF files, or corpus data.

The remote promotion checks out the public repository at the attested SHA, creates a
candidate env changing only the digest and release SHA, validates it, pulls the
attested digest, runs migrations and Qdrant provisioning, atomically updates the
Dev release pointer, and checks readiness. It never rebuilds, deletes volumes, or
runs `docker compose down`. If a migration succeeds but the application does not
become healthy, do not downgrade the database automatically; follow the exact-digest
rollback procedure after compatibility and backup review. A failed deployment can
be retried from the same completed workflow run after correcting the activation or
host condition.

If a post-migration failure leaves `/etc/cinegraph/dev.env.previous`, preserve that
mode-0600 file while investigating. Compare its recorded release with the current
digest, check migration compatibility, and restore the prior env/release pointer only
through the reviewed rollback procedure; never delete volumes or overwrite a GHCR
release tag to recover.

## Dev-first promotion

1. Verify Hostinger reports `x86_64`/`linux/amd64` and Docker supports the published platform.
2. Pull the verified digest into the Dev host and place it in `/etc/cinegraph/dev.env`.
3. Run `scripts/validate_vps_runtime.py`, database migration, Qdrant provisioning, and
   health/smoke checks using the exact digest.
4. Exercise authentication, guest Modern Family S1/S2 access, retrieval citations,
   and the non-guest corpus boundary.
5. Only after Dev passes, copy the same image digest and release SHA into the Prod
   env file and repeat the preflight and smoke checks. Never rebuild on the VPS.

## Rollback

Keep the prior known-good digest and release checkout. Roll back by changing only the
selected private environment values to that exact prior digest/SHA, validating the
file, and replacing the app after confirming migration compatibility. Do not use
`latest`, repoint a release tag, delete data volumes, or downgrade schema migrations
without a reviewed backup/compatibility plan.
