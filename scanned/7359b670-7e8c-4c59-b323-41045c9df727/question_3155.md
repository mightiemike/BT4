# Q3155: Signature-threshold acceptance bug in handleSecretsList

## Question
Can an unprivileged attacker provide create/update/delete/list params before and after auth stamping at `gateway JSON-RPC secrets request or POST /v2/vault/dkg_results/*` so `handleSecretsList` accepts invalid, duplicate, or insufficient signatures for a protected secrets action, causing retrieve sensitive data/files from a running server such as blockchain keys or vault secrets and violating namespace, owner, and request-ID stamping must not change what was actually authorized?

## Target
- File/function: core/capabilities/vault/gw_handler.go::handleSecretsList
- Entrypoint: gateway JSON-RPC secrets request or POST /v2/vault/dkg_results/*
- Attacker controls: create/update/delete/list params before and after auth stamping
- Exploit idea: Replay and cross-owner vault requests with namespace collisions and signature edge cases to prove whether secret access stays bound to what was actually authorized.
- Invariant to test: namespace, owner, and request-ID stamping must not change what was actually authorized
- Expected Immunefi impact: retrieve sensitive data/files from a running server such as blockchain keys or vault secrets
- Fast validation: Send cross-owner namespace collisions, replayed IDs, and duplicate signer sets; assert the request is rejected before any secret read/write/delete side effect.
