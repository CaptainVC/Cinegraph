# ADR-0017: Forced-command Dev host bootstrap and evidence

- Status: accepted for implementation
- Date: 2026-08-30

## Context

The Dev deployment workflow is activation-gated and the Hostinger VPS is not yet
reachable with an operator-approved key. The Phase 44 remote script also assumed the
SSH account could read the root-owned environment file and invoke Docker directly.
Adding that account to the Docker group would effectively grant general root access,
while making the environment file user-owned would weaken the host secret boundary.

## Decision

Keep `/etc/cinegraph/dev.env` root-owned with mode `0600`, keep the application and
release directories root-owned, and make the SSH account an unprivileged dedicated
account with no supplementary administrative or Docker groups. Its authorized key is
restricted to a root-owned forced-command dispatcher. The dispatcher parses exactly
one canonical `deploy <release-sha> <image-digest>` request without evaluation or
shell expansion, then supplies the two public identifiers over standard input to a
single no-argument root helper through a narrowly validated sudoers rule.

An operator runs `scripts/bootstrap_dev_host.py` from a reviewed checkout in the
Hostinger console. It accepts only an Ed25519 public key, an independently recorded
fingerprint, and the canonical port-22 host name/address. Apply mode creates only the
fixed account, restricted authorization, root-owned helpers, sudoers entry, fixed
directories, and a placeholder Dev env when absent. Existing differing state,
symlinks, unexpected ownership/modes/groups, or fingerprint mismatch fail closed.
Before installation, the checkout must be entirely root-controlled, clean including
untracked files, use only the approved public origin, and exactly match its live
`main` tip. The account uses an invalid non-locking password hash so password
authentication cannot succeed while Ubuntu OpenSSH can still authorize its forced
public key.
Check mode performs no mutation and additionally runs the existing Dev runtime and
Compose validator, so placeholder or malformed environment state cannot become
activation evidence. Successful output contains only public evidence,
including the server Ed25519 public-key fingerprint and exact known-hosts line.

The root helper remains intentionally powerful enough to update the bounded Dev
runtime, pull public immutable images, run migrations/provisioning, and start the
stack. It accepts no arguments, validates its root-owned path chain repeatedly, reads
exactly two newline-terminated public identifiers, and rejects extra input. Before
mutation it verifies that the release belongs to the repository's `main` history and
that the pulled image source/revision labels match the approved repository and SHA.
For an OCI index digest, it creates but never starts a disposable probe container,
inspects the Engine-selected platform image ID, and removes the probe before runtime
services or migrations.
It cannot be used as a general sudo or Docker command.

## Consequences

The private deployment key remains off-host except for the protected GitHub
Environment secret; only its public key is installed through the console. The
operator must compare the console-produced host fingerprint with an independent
Hostinger view before configuring known-hosts and must populate the root-owned Dev env
manually. The deployment account cannot run arbitrary commands, obtain a shell, use
Docker directly, or pass arguments to the privileged helper.

The forced helper is a bounded root mutation boundary, not a sandbox. Its source,
sudoers rule, authorized-key entry, ownership, and modes require review together.
This phase does not install packages, change sshd/firewall settings, activate the
GitHub Environment, access the VPS remotely, transfer secrets/corpus, deploy Prod, or
configure DNS/TLS.
