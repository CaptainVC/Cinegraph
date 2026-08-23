# Cinegraph contribution boundaries

- Preserve the hexagonal direction: domain depends only on domain/common; application depends on domain and ports; adapters/bootstrap wire concrete providers at the edge.
- Keep configuration and user-facing literals centralized in `src/cinegraph/config` and `src/cinegraph/common`; do not duplicate policy values in adapters.
- Authorize spoiler visibility and corpus entitlement before retrieval; never let model-visible arguments widen trusted scope.
- Never commit private corpus data, secrets, provider payloads, or derived private artifacts to Git, logs, or images.
- Use deterministic IDs and explicit version/revision values for persisted or evaluated artifacts.
- Do not write media or trigger provider actions without explicit human-in-the-loop approval and audit evidence.
- Add focused unit/contract/architecture tests for behavior changes; keep the branch quality contract green.
- Start each phase from updated `main` and keep one phase per pull request.
