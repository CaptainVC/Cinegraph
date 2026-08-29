# Security and quality gates

Phase 41 adds zero-cost, repository-native checks for this public repository. The
authoritative merge gates are the existing test/lint/type/build jobs plus Bandit,
locked dependency audit, CodeQL, secret scan, and Compose validation. CodeRabbit and
SonarQube Cloud are advisory integrations and are not required for ordinary CI until
their external accounts/apps are deliberately activated.

## Authoritative checks

- **Bandit SAST** scans `src` and `scripts` at medium-or-higher severity and confidence.
  Tests, knowledge, build, and virtual-environment directories are excluded in the
  centralized `pyproject.toml` configuration. The only `# nosec` annotations must stay
  next to the two narrowly reviewed cases: an explicitly validated HTTP(S) Qdrant URL
  and the intentional container-wide API bind that is published on host loopback only.
- **pip-audit** runs against a `uv export --locked --no-dev --no-emit-project` requirements file, so the
  production dependency graph—not the developer workstation—is audited. It fails on
  known vulnerabilities and has a finite network timeout; no vulnerability ignores or
  credentials are configured. Local `scripts/quality.py` does not invoke this network
  audit; run the CI command explicitly when needed.
- **CodeQL** uses the official GitHub action on pull requests, pushes to `main`, and a
  weekly schedule. It has read-only repository access plus only the security-events
  permission needed to publish results. It receives no OpenAI, VPS, or other secrets,
  including on untrusted pull requests.
- The pre-commit `GitHub Actions use immutable commit pins` hook rejects new remote
  `uses:` references that are not full 40-character commit SHAs. Version comments next
  to each pin remain the human-readable upgrade record.

## Advisory integrations

The checked-in `sonar-project.properties` is ready for SonarQube Cloud analysis with
Python source/test paths and coverage XML. Its Python 3.12–3.14 setting matches the
repository's declared support and current runtime. No organization key, project key,
token, or Sonar workflow is invented here. After creating the exact public-repository project,
store `SONAR_TOKEN` as a protected Actions secret and add an opt-in workflow/job that
only runs when that secret exists; absence must be a neutral skip. Do not make ordinary
CI depend on an external account before activation.

CodeRabbit is configured conservatively in `.coderabbit.yaml` using its documented v2
schema, a chill profile, draft exclusion, and filters for private/generated/vendor
artifacts. It is advisory: reviews can inform a maintainer but cannot replace tests,
SAST, dependency audit, or human approval. Its hosted GitHub App requires broad
read-write contents/status/issues/pull-request access; mitigate that risk by installing
it only on the Cinegraph repository, reviewing its current permissions before approval,
and never giving it deployment or secret-management credentials.

Free-plan assumptions are external and may change: public-repository CodeQL,
CodeRabbit, and SonarQube Cloud availability, quotas, and features must be rechecked
at activation time. The repository does not treat those assumptions as security
controls.

## Activation, rollback, and disablement

1. Merge this phase and observe the exact stable checks. Add the checks as required
   branch-protection contexts only after they have completed successfully on `main`.
2. For CodeRabbit, install/restrict the App to Cinegraph, review one PR, and leave it
   advisory unless a later policy explicitly makes it required.
3. For Sonar, create the exact project, add the protected token, run an opt-in analysis,
   review the quality profile/coverage exclusions, and only then decide whether a
   quality gate should become required.
4. To roll back, remove the optional App/secret or disable its workflow/job; the native
   Bandit, pip-audit, CodeQL, tests, and secret scan remain active. Never disable a
   failing authoritative security check by adding an ignore without a reviewed ADR.

The CodeQL and external-review tools are analysis only. They do not deploy, mutate the
VPS, read ignored corpus files, or receive API keys.
