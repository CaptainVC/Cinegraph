# Dev activation checklist

This runbook controls the first real Dev deployment. It is a stop/go procedure, not
authorization to access Hostinger or GitHub. Obtain action-time confirmation before
each browser launch, Hostinger mutation, key transfer, GitHub Environment mutation,
repository activation, and merge that can trigger deployment.

## Evidence boundary

Start from `docs/evidence/dev-activation.example.json` and validate a sanitized copy:

```bash
python -B -m scripts.validate_dev_activation_evidence <sanitized-evidence.json>
```

The record may contain only public release identifiers (including the exact immutable
image tag), GitHub Actions run URLs, success conclusions, the public host-key
fingerprint, attestation identity, readiness result, and UTC observation time. Keep
the full bootstrap JSON in protected operator
records. Never record the host address, known-hosts line, deployment public or private
key, API keys, environment contents, SRT/PDF/corpus locations, provider payloads, or
live secret values in Git, PR text, workflow logs, or the sanitized record.

## Gate 1: reviewed repository state

Stop unless all conditions pass:

1. Phase 45 is present on `main`, the Phase 46 change is reviewed, and required checks
   are green.
2. The immutable GHCR package is public and an anonymous exact-digest pull is possible.
3. The intended host is Linux `x86_64` with the Phase 45 required tools, Docker Engine,
   and Compose v2. Bootstrap installs neither packages nor host policy.
4. A dedicated Ed25519 deployment key exists. Its private half remains off-host; only
   its single canonical public line may be transferred through the confirmed console.
5. The independently recorded public-key fingerprint is available for comparison.

An existing key that merely failed against another account is not proof that it is
the intended dedicated deployment identity.

## Gate 2: Hostinger console bootstrap

Obtain confirmation immediately before opening the authenticated browser profile and
again before changing the VPS. Stop if the expected authenticated session is absent;
do not paste credentials into chat or switch to an unapproved session.

From the Hostinger console as root:

1. Create a fresh root-owned clone of the public repository at exact live `main`.
2. Place only the deployment public key in a root-owned, regular non-symlink file.
3. Compare its `SHA256:` fingerprint with the independently recorded value.
4. Run the Phase 45 bootstrap in apply mode, using `python3 -B -m`.
5. Compare the emitted server Ed25519 public fingerprint independently. Any mismatch
   is a hard stop; never learn a replacement identity from a failed SSH connection.
6. Populate root-owned mode-0600 `/etc/cinegraph/dev.env` manually. Do not print it.
7. Run bootstrap again with `--check`. Stop on any placeholder, permission, platform,
   Compose, repository, or runtime-contract failure.

If the installed static helper differs from the reviewed checkout, the normal apply
and `--check` modes intentionally refuse to overwrite it. After confirming the
checkout is clean and exactly matches live `main`, use the explicit operator-only
refresh once:

```bash
sudo python3 -B -m scripts.bootstrap_dev_host \
  --public-key-file <root-owned-public-key-file> \
  --expected-key-fingerprint SHA256:<operator-recorded-public-key-fingerprint> \
  --host <canonical-vps-host> --refresh-deploy-code
sudo python3 -B -m scripts.bootstrap_dev_host \
  --public-key-file <root-owned-public-key-file> \
  --expected-key-fingerprint SHA256:<operator-recorded-public-key-fingerprint> \
  --host <canonical-vps-host> --check
```

The refresh replaces only changed dispatcher/helper files, using an atomic replacement
for each file, then revalidates their root:root `0755` contract and the runtime. It
never refreshes the environment, `authorized_keys`, or sudoers content.

Bootstrap must leave the dedicated account without Docker, sudo, admin, or other
supplementary groups. The account receives only the root-owned forced dispatcher and
the no-argument sudo path to the bounded root helper.

The installed `/usr/local/sbin/cinegraph-deploy-dev` is a static host copy; reviewed
repository changes do not update it automatically. Before a helper-changing release
can reach the VPS, an operator must refresh it from reviewed live `main` through the
operator refresh/check procedure. The helper verifies that the tracked public
`knowledge/catalogue.json` is root-owned and mode `0644` before any image pull or
database mutation. Its read-only Compose bind mount can then be read by the UID 10001
app container while all other checkout files retain the restrictive deployment umask.

After the exact image identity is verified, the helper runs an egress-only embedding
warmup before starting dependencies or running migrations. The one-shot receives no
OpenAI key, database setting, Qdrant setting, or corpus mount. It materializes both
configured FastEmbed models in the persistent non-root `app-cache` volume and performs
a fixed, corpus-free dense/sparse sanity encode. Only the one-shot redirects generic
model-download temporary files to that volume; application temp files keep using the
hardened 64 MiB `/tmp` tmpfs. The application mounts the completed model cache
read-only and forces Hugging Face offline mode, so serving cannot drift models or
silently repair an incomplete deployment from the network.

## Gate 3: forced-command probe

Obtain confirmation before using the private deployment key. From the operator
machine, connect with strict host-key checking, the independently pinned known-hosts
file, batch mode, identities-only, no agent forwarding, and the dedicated account.
Send an intentionally invalid command—not a syntactically valid deployment request.

Go only when authentication reaches the dispatcher and returns exactly the documented
unauthorized-deployment response. Stop on a password prompt, host-key mismatch,
generic public-key denial, shell access, arbitrary command execution, or access to
Docker/sudo beyond the forced boundary.

## Gate 4: protected GitHub Environment

Obtain confirmation immediately before GitHub mutations. Create/protect the exact
Environment `dev`, restrict deployment to `main`, and configure its required reviewer.
Add only the four values named in the image-release runbook: two environment variables
for the host and fixed deployment account, and two secrets for the private key and
exact pinned known-hosts line. Keep the private key in that Environment only.

The repository-level `CINEGRAPH_DEV_DEPLOY_ENABLED` flag must remain absent or not
`true` until the host check, forced-command probe, Environment review, and PR checks
all pass. Environment configuration is not evidence that deployment is active.

## Gate 5: activate last

Obtain separate confirmation before changing the repository variable and before
merging the triggering PR.

1. Reconfirm every earlier gate and the Environment values.
2. Set `CINEGRAPH_DEV_DEPLOY_ENABLED` to exactly `true`.
3. Merge the reviewed Phase 46 PR. Enabling the flag does not replay an older
   `workflow_run`; the merge supplies the new Quality-to-publish-to-Dev chain.
4. Observe `Quality`, `Publish immutable image`, then `Deploy Dev` without substituting
   a SHA or digest manually.
5. Confirm the release SHA is the publisher head SHA, the registry digest belongs to
   its immutable tag, GitHub verifies the expected signer and `main` source ref, and
   the remote helper reports bounded readiness.
6. Confirm the helper's secret-free post-deploy smoke gate passes. It uses only the
   loopback Dev origin, discovers and validates the deployed API prefix through the
   public `/client-config` contract, obtains a guest session, retains the issued
   cookie, and reads the configured catalogue endpoint. It must observe the approved
   guest scope revision and schema plus exactly the canonical `Modern Family` series
   with exactly Seasons 1 and 2. The gate never calls the answer/RAG endpoint and
   never prints response bodies, tokens, or corpus data. A failure occurs before
   deployment success cleanup, preserving the existing fail-closed recovery state.
7. Validate the sanitized evidence record. Attach only safe evidence to the merged PR
   or protected operational record; do not create a second deployment merely to edit
   evidence in Git.

## Stop, disable, and recover

- Before migration, set the activation variable away from `true`, correct the
  configuration, and rerun only an immutable, still-valid workflow chain or merge a
  new reviewed change.
- Never overwrite/delete a GHCR release tag or substitute a different digest.
- If `/etc/cinegraph/dev.env.previous` exists, preserve it and stop repeated mutation
  until the earlier failure is understood.
- For the catalogue permission failure, stop the app and inspect the release checkout
  and container logs without deleting volumes. Confirm the exact release contains the
  tracked regular `knowledge/catalogue.json` and that the reviewed helper normalized
  only that public file to mode `0644`; do not broaden permissions on the checkout.
- The current failed activation must be diagnosed and recovered in place. Do not delete
  volumes, auto-downgrade migrations, or retry until the static installed helper has
  been refreshed and the prior failure is understood.
- If model download reports `No space left on device` while host disk and inodes are
  healthy, inspect the container tmpfs and model cache separately. Do not enlarge the
  tmpfs or delete volumes reflexively. Verify the reviewed warmup uses the persistent
  cache/temp paths and completes before allowing migrations or app startup.
- After migration starts, do not automatically roll back the database. Review schema
  compatibility and backups before selecting a previous exact digest and SHA.
- Never delete volumes, use `compose down -v`, downgrade migrations casually, accept a
  changed host key from SSH output, or expose private logs as evidence.
- For suspected key or secret disclosure, disable activation, rotate the affected
  material, preserve sanitized incident timing, and re-run every trust gate.

## Non-goals

This phase does not configure Prod, DNS, TLS, reverse proxying, firewall/sshd policy,
OS packages, backups, automatic database rollback, or a deployment workflow redesign.
Third-party configuration storage and corpus-data transfer or ingestion are also
excluded. Repository changes do not themselves access Hostinger, mutate a GitHub
Environment, or prove Dev is live.
