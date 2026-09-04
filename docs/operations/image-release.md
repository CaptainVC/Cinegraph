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

The package must be public before Dev activation. The first package push may require
repository/package visibility and organization policy activation in GitHub. Verify an
anonymous pull of the exact digest from Dev; the host bootstrap and root helper do not
store GHCR credentials. Do not put a PAT, registry token, or Docker credential into
the deployment account or workflow.

## Dev deployment activation

Phase 44 adds an activation-gated `Deploy Dev` workflow. It is intentionally skipped
unless the repository-level Actions variable `CINEGRAPH_DEV_DEPLOY_ENABLED` is
exactly `true`; an absent or different value cannot open an SSH connection and does
not fail `main` or image publication. The workflow is Dev-only and has no Prod job.
Use the minute, stop/go [Dev activation checklist](dev-activation.md) for the first
real deployment. Its action-time confirmations and sanitized-evidence boundary are
mandatory; repository documentation is not authorization to mutate external systems.

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
settings. The Dev env, `/etc/cinegraph`, `/opt/cinegraph`, dispatcher, and privileged
helper remain root-owned. The SSH account is the password-disabled `cinegraph-deploy` account
with no Docker, sudo, admin, or other supplementary groups. Its only authorization is
the forced dispatcher; it cannot receive a shell or invoke arbitrary Docker/sudo
commands. Its home, `.ssh` directory, and public `authorized_keys` file are
root-managed and not writable by the account. The workflow sends one canonical
`deploy <sha> <digest>` command.

Run the reviewed host bootstrap from the Hostinger console before configuring
GitHub. Use a fresh root-owned clone exactly at the live `main` tip; bootstrap rejects
dirty, untracked, non-root-controlled, stale, or differently sourced checkouts:

```bash
sudo python3 -B -m scripts.bootstrap_dev_host \
  --public-key-file /root/cinegraph-deploy.pub \
  --expected-key-fingerprint SHA256:<operator-recorded-public-key-fingerprint> \
  --host <canonical-vps-host>
sudo python3 -B -m scripts.bootstrap_dev_host \
  --public-key-file /root/cinegraph-deploy.pub \
  --expected-key-fingerprint SHA256:<operator-recorded-public-key-fingerprint> \
  --host <canonical-vps-host> --check
```

The input is the public Ed25519 key only; never place the private key on the VPS.
Bootstrap does not install packages, change sshd/firewall policy, overwrite an
existing env or authorized-keys file, or activate deployment. Its successful JSON is
safe evidence containing the bootstrap SHA, account/mode, server Ed25519 public
fingerprint, and exact port-22 known-hosts line. Compare that fingerprint through an independent
Hostinger console view before adding the line to GitHub; never use `ssh-keyscan`.
Apply mode may create the placeholder Dev env; `--check` additionally runs the
existing fail-closed runtime/Compose validator and will not pass until every private
placeholder, exact release SHA, and public image digest has been populated.

Populate the root-owned `/etc/cinegraph/dev.env` manually, rerun `--check`, create and
protect the `dev` Environment, configure its values, and flip the repository
activation variable last. This PR does not create the Environment, access the VPS,
transfer secrets, SRT/PDF files, or corpus data.

Enabling the repository variable does not replay an earlier publisher completion.
For first activation, enable it only after every host and Environment gate passes,
then merge a reviewed change so a new Quality-to-publish-to-Dev workflow chain carries
one exact release SHA and digest.

Before activation, use the deployment private key from the operator machine to send
an intentionally invalid command. Authentication is proven only when the server
returns `SSH command is not an authorized deployment request`; a password prompt,
generic public-key denial, shell, or any other result fails the activation check. Do
not send a syntactically valid deploy command during this probe.

The Dev workflow gives OpenSSH three bounded connection-establishment attempts,
with a ten-second timeout per attempt, to absorb a transient runner-to-VPS TCP/22
timeout. This is intentionally transport-level resilience: there is one SSH
process and one remote deployment command, with no retry loop around an
authenticated session. During a long migration or warmup, a fifteen-second
server-alive probe and two missed responses protect the session from a dead
connection without replaying the command. These settings are committed as
workflow transport-policy constants; change them only with the pinned-SSH
contract tests and ADR rationale updated together.

The root helper reads only the canonical SHA and digest from the forced dispatcher,
checks out the public repository at the attested SHA, creates a
candidate env changing only the digest and release SHA, validates it, pulls the
attested digest, verifies and normalizes the tracked public catalogue manifest for
the UID 10001 read-only bind mount, and warms both configured embedding models before
starting dependencies or mutating schemas. The warmup has egress plus the persistent
model cache only; it receives no OpenAI key, backend credentials, Qdrant settings, or
corpus mount. It performs a fixed corpus-free dense/sparse sanity encode and fails the
promotion before migration if the cache is unusable. The serving app receives that
cache read-only with Hugging Face offline mode enabled. The helper then runs migrations
and Qdrant provisioning, atomically updates the Dev release pointer, checks readiness,
and runs the secret-free guest entitlement smoke contract. The smoke is loopback-only,
retains the real auth cookie, verifies the configured API prefix and approved guest scope,
and requires exactly the canonical
Modern Family series with Seasons 1 and 2, and never calls the answer/RAG endpoint
or prints response bodies. It never rebuilds, deletes volumes, or
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

The root helper is installed as a static host file and is not updated by a repository
checkout or merge. Before deploying this fix, the operator must refresh
`/usr/local/sbin/cinegraph-deploy-dev` from reviewed live `main` using the bootstrap
and `--check` procedure. The current failed activation must be diagnosed and recovered
without deleting volumes or automatically downgrading migrations; preserve
`dev.env.previous` until the failure and migration compatibility are reviewed.
If the installed helper still contains the static Phase 46 bytes, use the explicit
operator-only `--refresh-deploy-code` path only after the root-controlled checkout
exactly matches live `main`, then rerun `--check`; ordinary apply/check modes do not
overwrite reviewed host files.

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
