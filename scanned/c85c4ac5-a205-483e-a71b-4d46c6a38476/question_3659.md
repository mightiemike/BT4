# Q3659: Boundary preservation edge case in newActiveRequest #4

## Question
Can an unprivileged attacker use create/update/delete/list params before and after auth stamping at `gateway JSON-RPC secrets request or POST /v2/vault/dkg_results/*` so `newActiveRequest` reaches a concrete path to authentication bypass into protected secret-management actions by breaking the invariant that replay, signature, and threshold checks must reject duplicate or cross-owner requests, rather than merely causing an out-of-scope theoretical issue?

## Target
- File/function: core/services/gateway/handlers/vault/handler.go::newActiveRequest
- Entrypoint: gateway JSON-RPC secrets request or POST /v2/vault/dkg_results/*
- Attacker controls: create/update/delete/list params before and after auth stamping
- Exploit idea: Replay and cross-owner vault requests with namespace collisions and signature edge cases to prove whether secret access stays bound to what was actually authorized.
- Invariant to test: replay, signature, and threshold checks must reject duplicate or cross-owner requests
- Expected Immunefi impact: authentication bypass into protected secret-management actions
- Fast validation: Send cross-owner namespace collisions, replayed IDs, and duplicate signer sets; assert the request is rejected before any secret read/write/delete side effect.
