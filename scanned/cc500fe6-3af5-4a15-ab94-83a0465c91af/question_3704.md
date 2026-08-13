# Q3704: Secret-identifier collision in VerifyDKGResult

## Question
Can an unprivileged attacker use duplicate digests, replay timing, and stale authorized params at `POST /v2/vault/dkg_results/verify` so `VerifyDKGResult` normalizes distinct secret identifiers into the same storage key, causing authentication bypass into protected secret-management actions and violating replay, signature, and threshold checks must reject duplicate or cross-owner requests?

## Target
- File/function: core/web/vault_controller.go::VerifyDKGResult
- Entrypoint: POST /v2/vault/dkg_results/verify
- Attacker controls: duplicate digests, replay timing, and stale authorized params
- Exploit idea: Replay and cross-owner vault requests with namespace collisions and signature edge cases to prove whether secret access stays bound to what was actually authorized.
- Invariant to test: replay, signature, and threshold checks must reject duplicate or cross-owner requests
- Expected Immunefi impact: authentication bypass into protected secret-management actions
- Fast validation: Send cross-owner namespace collisions, replayed IDs, and duplicate signer sets; assert the request is rejected before any secret read/write/delete side effect.
