# Q3691: Namespace or owner confusion in ExportDKGResult

## Question
Can an unprivileged attacker exploit owner/workflow binding fields and prefixed request IDs at `POST /v2/vault/dkg_results/export` so `ExportDKGResult` authorizes one namespace/owner but reads, overwrites, or deletes another, causing authentication bypass into protected secret-management actions and violating replay, signature, and threshold checks must reject duplicate or cross-owner requests?

## Target
- File/function: core/web/vault_controller.go::ExportDKGResult
- Entrypoint: POST /v2/vault/dkg_results/export
- Attacker controls: owner/workflow binding fields and prefixed request IDs
- Exploit idea: Replay and cross-owner vault requests with namespace collisions and signature edge cases to prove whether secret access stays bound to what was actually authorized.
- Invariant to test: replay, signature, and threshold checks must reject duplicate or cross-owner requests
- Expected Immunefi impact: authentication bypass into protected secret-management actions
- Fast validation: Send cross-owner namespace collisions, replayed IDs, and duplicate signer sets; assert the request is rejected before any secret read/write/delete side effect.
