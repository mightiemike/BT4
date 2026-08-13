# Q3243: Request-ID stamping mismatch in EnsureRightLabelOnSecret

## Question
Can an unprivileged attacker abuse signature bundles, signer sets, and threshold parameters at `gateway JSON-RPC secrets request or POST /v2/vault/dkg_results/*` so `EnsureRightLabelOnSecret` validates, authorizes, and stores different effective request identities, leading to unauthorized secret overwrite/delete or workflow-owner action and breaking secret identifiers must not collide across normalized namespaces or owners?

## Target
- File/function: core/capabilities/vault/validator.go::EnsureRightLabelOnSecret
- Entrypoint: gateway JSON-RPC secrets request or POST /v2/vault/dkg_results/*
- Attacker controls: signature bundles, signer sets, and threshold parameters
- Exploit idea: Replay and cross-owner vault requests with namespace collisions and signature edge cases to prove whether secret access stays bound to what was actually authorized.
- Invariant to test: secret identifiers must not collide across normalized namespaces or owners
- Expected Immunefi impact: unauthorized secret overwrite/delete or workflow-owner action
- Fast validation: Send cross-owner namespace collisions, replayed IDs, and duplicate signer sets; assert the request is rejected before any secret read/write/delete side effect.
