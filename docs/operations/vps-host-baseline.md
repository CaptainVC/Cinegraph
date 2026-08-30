# VPS host baseline

This document defines the Phase 40 single-host runtime contract. Phase 44 adds an
activation-gated Dev promotion workflow, but this document remains the host-side
contract: DNS, TLS termination, and corpus transfer are separate phases. The stack
is safe to run behind a future reverse proxy because
only a configurable loopback port is published by Compose.

## Layout and isolation

Use a dedicated deployment account and keep the application outside a user's home:

```text
/opt/cinegraph/
  releases/<git-sha>/       # immutable checked-out/build context
  current -> releases/...   # selected release
  shared/                   # operator-owned non-Git runtime files
/etc/cinegraph/
  dev.env                   # mode 0600, development secrets/config
  prod.env                  # mode 0600, production secrets/config
/var/backups/cinegraph/     # encrypted/off-host database backups
```

`/opt/cinegraph`, `releases`, and `shared` are root:root mode `0750`.
`/etc/cinegraph` is root:root mode `0700`; `dev.env` is root:root mode `0600`.
The root-owned dispatcher and helper are installed at
`/usr/local/libexec/cinegraph-deploy-dispatch` and
`/usr/local/sbin/cinegraph-deploy-dev`. The dedicated SSH account cannot read the env
or mutate these paths directly.

Each environment has its own Compose project name, named volumes, database, Qdrant
collection, loopback port, and env file. Do not reuse a production volume in Dev.
PostgreSQL and Qdrant are on an internal-only Docker network and have no host ports.
The app additionally has a dedicated egress network because it must call OpenAI and
may need to populate its persistent FastEmbed model cache; that network is not attached
to either data service. The app runs as UID/GID 10001,
drops Linux capabilities, uses a read-only root
filesystem, and has bounded memory/CPU/PID settings. The API supervisor remains a
single process (`scripts/run_api.py`); do not scale the app service horizontally.

## First install (operator-run)

Install Docker Engine and the Compose v2 plugin from the vendor-supported packages.
The dedicated `cinegraph-deploy` user is password-disabled and must have no Docker, sudo, admin,
or other supplementary groups. A root-owned forced-command dispatcher may invoke only
the no-argument root helper through the validated sudoers rule. This narrow helper
owns the bounded `/opt` and `/etc` mutation and Docker operations. The account's home,
`.ssh` directory, and public authorization file are root-managed and not writable by
the deployment identity.
The baseline requires at least 4 GiB of memory and 20 GiB of free disk so the three
bounded services, model cache, images, and operational headroom do not begin in an
overcommitted state. Check that the selected loopback port is available. Clone a
reviewed commit, run `scripts/bootstrap_dev_host.py` from the Hostinger console, and
populate the created root-owned `/etc/cinegraph/dev.env`. Replace every `REPLACE_*`
value, including
setting `CINEGRAPH_IMAGE` to the approved GHCR name, `CINEGRAPH_IMAGE_DIGEST=sha256:<the
exact 64-character image digest>`, and `CINEGRAPH_RELEASE_SHA=<the corresponding
40-character Git SHA>`, set mode `0600`,
and ensure `CINEGRAPH_ENV_FILE` points back to that exact file. Prod host preparation
remains manual and outside the Dev bootstrap.

For the first Dev host, follow the stop/go [Dev activation checklist](dev-activation.md).
It requires explicit confirmation at each external-action boundary, an independent
host-key comparison, a forced-command authentication probe, activation last, and only
sanitized evidence. Never commit the bootstrap output, known-hosts line, host address,
key material, environment contents, or corpus locations.

Run the fail-closed preflight before rendering or starting anything:

```bash
cd /opt/cinegraph/current
python3 scripts/validate_vps_runtime.py \
  --environment production \
  --env-file /etc/cinegraph/prod.env \
  --compose-file deploy/compose.yaml
```

The validator checks required settings, production service modes, secret placeholders,
env-file permissions, Docker/Compose availability, and Compose rendering. It never
prints secret values. On Windows or another platform without POSIX permission bits,
the operator must enforce equivalent ACLs manually.

During an in-place upgrade the existing app may still own the published port. In that
case use the explicit `--allow-active-port` flag, then stop/replace the app as part of
the reviewed upgrade procedure; it is not a general port-check bypass.

Compose intentionally does not pass the private env file wholesale. The API receives
only its runtime settings and OpenAI key; PostgreSQL receives only its `POSTGRES_*`
settings; migration receives database/service settings; and Qdrant provisioning
receives only Qdrant connection settings. This least-privilege mapping prevents an
OpenAI key from entering dependency or migration containers.

## First start and schema provisioning

The API does not auto-migrate or auto-create the Qdrant collection. Start dependencies,
run each explicit one-shot operation, then start the app. The provisioning command
retries Qdrant's `/readyz` endpoint because the official Qdrant image intentionally does
not include curl/wget. The committed catalogue manifest is bind-mounted read-only from
the selected release, while private/derived knowledge remains in the named volume:

```bash
docker compose --env-file /etc/cinegraph/prod.env -f deploy/compose.yaml up -d postgres qdrant
docker compose --env-file /etc/cinegraph/prod.env -f deploy/compose.yaml pull app
docker compose --env-file /etc/cinegraph/prod.env -f deploy/compose.yaml --profile migration run --rm migrate
docker compose --env-file /etc/cinegraph/prod.env -f deploy/compose.yaml --profile provisioning \
  run --rm provision-qdrant
docker compose --env-file /etc/cinegraph/prod.env -f deploy/compose.yaml up -d app
curl --fail http://127.0.0.1:18001/health/ready
```

The Qdrant command is idempotent but mutating; run it only after reviewing the
collection name and environment. Corpus ingestion remains a separately authorized
operation and must write only to the intended environment volume.

If the PostgreSQL password contains URL-reserved characters, percent-encode it in
`CINEGRAPH_DATABASE_URL` while keeping the raw value in `POSTGRES_PASSWORD`; the
preflight compares the decoded URL credential without printing either value.

## Upgrade, rollback, and backups

Pull the exact published digest, run preflight, start dependencies, apply migrations,
provision/verify Qdrant, and then replace the app container. Keep the previous image
until health and a smoke query pass. Rollback means selecting the previous digest, not
deleting volumes; never downgrade a database migration without an explicit backup and
compatibility review. See [the image release runbook](image-release.md).

For a known-good rollback, first verify that the prior digest is still available. Set
`CINEGRAPH_IMAGE_DIGEST` and `CINEGRAPH_RELEASE_SHA` in the private environment file,
atomically repoint
`/opt/cinegraph/current` to `/opt/cinegraph/releases/<previous-sha>`, change into that
directory, run preflight with `--allow-active-port`, and replace only the API:

```bash
docker compose --env-file /etc/cinegraph/prod.env -f deploy/compose.yaml \
  up -d --no-deps app
curl --fail http://127.0.0.1:18001/health/ready
```

The image name, digest, and release SHA must match the reviewed release; floating tags
such as `latest` are rejected by preflight.

Back up PostgreSQL with `pg_dump` (custom format, encrypted, and copied off-host) and
record the Git SHA, schema revision, collection name, and backup timestamp. Qdrant's
named volume must be snapshotted or backed up consistently with PostgreSQL; restore
both from the same application/data revision. Test restoration on Dev before declaring
Prod recoverable. Do not include SRT, PDF, API keys, or derived corpus artifacts in Git
or backup logs.

## Incident steps

1. Check `docker compose ps`, container health, disk space, and `docker compose logs`
   for metadata only; redact credentials and user/corpus content.
2. If dependencies are unhealthy, keep the app stopped and inspect their volumes and
   resource limits. Do not delete volumes during diagnosis.
3. If the app is unhealthy after a release, stop only the app, preserve data volumes,
   and roll back to the last known-good image/release.
4. Rotate exposed secrets, invalidate sessions as appropriate, and preserve the
   incident timeline and Git SHA. Restore data only after integrity checks.

## Known limits and activation gap

This baseline owns one host and one API process. It does not provide HA, autoscaling,
zero-downtime migrations, external secret management, backups by itself, a reverse
proxy, TLS, DNS, or Prod deployment. Hostinger access, firewall policy, domain
ownership, and an operator-approved SSH key must be activated and verified before any
remote mutation. The Phase 44 Dev workflow remains skipped until the repository
activation variable is exactly `true` and the protected `dev` Environment contains
the pinned SSH material. Phase 45 supplies an operator-run console bootstrap/check;
the PR itself does not access the VPS, configure that Environment, install packages,
change sshd/firewall policy, or transfer private corpus/API-key data. The private key
remains off-host and is stored only in the protected GitHub Environment. The runtime
requires `x86_64`/`linux/amd64`.

## Image update cadence

Base and dependency images are pinned by both a readable version tag and an immutable
multi-architecture digest. Review security releases monthly and immediately for a
critical CVE; update the tag and digest together in a dedicated PR, render Compose,
build and scan the image, then promote the same digest through Dev before Prod. Do not
silently float `latest` or update a running host in place.
