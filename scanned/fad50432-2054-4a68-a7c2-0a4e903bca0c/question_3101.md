# Q3101: Namespace or owner confusion in HandleGatewayMessage

## Question
Can an unprivileged attacker exploit owner/workflow binding fields and prefixed request IDs at `gateway JSON-RPC secrets request or POST /v2/vault/dkg_results/*` so `HandleGatewayMessage` authorizes one namespace/owner but reads, overwrites, or deletes another, causing authentication bypass into protected secret-management actions and violating replay, signature, and threshold checks must reject duplicate or cross-owner requests?

## Target
- File/function: core/capabilities/vault/gw_handler.go::HandleGatewayMessage
- Entrypoint: gateway JSON-RPC secrets request or POST /v2/vault/dkg_results/*
- Attacker controls: owner/workflow binding fields and prefixed request IDs
- Exploit idea: Replay and cross-owner vault requests with namespace collisions and signature edge cases to prove whether secret access stays bound to what was actually authorized.
- Invariant to test: replay, signature, and threshold checks must reject duplicate or cross-owner requests
- Expected Immunefi impact: authentication bypass into protected secret-management actions
- Fast validation: Send cross-owner namespace collisions, replayed IDs, and duplicate signer sets; assert the request is rejected before any secret read/write/delete side effect.
