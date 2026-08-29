# ADR-0013: Isolated single-host container runtime baseline

- Status: accepted for implementation
- Date: 2026-08-29

## Context

Cinegraph needs a repeatable development and production runtime on one Hostinger VPS
while keeping private corpus and credentials outside Git. The API's durable job
supervisor is intentionally single-process, and production requires PostgreSQL and a
remote Qdrant service. The host may be shared, so accidental public ports and broad
filesystem ownership are unacceptable.

## Decision

Ship a pinned multi-stage Python/uv image and a Compose stack containing one API,
PostgreSQL 16, and Qdrant. Compose publishes only a configurable loopback API port;
PostgreSQL and Qdrant are reachable only over an internal network. App and one-shot
migration/provisioning containers run non-root with read-only roots, dropped
capabilities, no-new-privileges, bounded resources, and named persistent volumes.
Development and production use separate project names, env files, collections, and
volumes. Required secret settings are supplied by a mode-0600 operator env file and
never committed or printed. Database migration and Qdrant collection provisioning are
explicit operator commands; API startup never performs either mutation.

## Consequences

This is a clear contract for later GitHub Actions and reverse-proxy work, and gives
safe defaults on a shared VPS. It is not high availability: one API process and one
host remain failure domains. Docker/Compose, host backups, firewalling, TLS, external
secret storage, and SSH activation remain operational responsibilities and later
phases. Qdrant's collection schema must be provisioned before readiness can pass.
