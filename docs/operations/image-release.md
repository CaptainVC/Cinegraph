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
