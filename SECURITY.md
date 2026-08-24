# Security Policy

## Supported Versions

Security fixes are applied to the current `main` branch.

## Reporting A Vulnerability

Do not open a public issue for a suspected security vulnerability.

Use GitHub private vulnerability reporting for this repository. If that option is
unavailable, contact the repository owner privately through their GitHub profile
with a concise description, reproduction steps, impact, and any suggested
mitigation.

Please do not include private subtitle content, provider tokens, user data, or
production URLs in a report.

## Scope

Security-sensitive areas include:

- household, user, profile, and spoiler-boundary authorization;
- vector, graph, and source-document visibility filtering;
- untrusted subtitle and summary content used near LLM workflows;
- media-provider tokens and command authorization;
- file uploads, parsers, and external HTTP adapters;
- logs, traces, backups, and source/version provenance records.
- session rotation, owner-scoped account/profile/session management, CSRF, and
  same-origin enforcement. Production uses `__Host-` cookies; never report raw
  cookies, session tokens, CSRF values, passwords, or hashes in tickets.

## Repository Safeguards

The repository uses CI for tests and package builds, pre-commit hygiene hooks,
and automated secret scanning. Private corpus data, review ledgers, environment
files, provider credentials, and local notebooks must never be committed.
