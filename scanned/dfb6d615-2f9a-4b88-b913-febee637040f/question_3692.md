# Q3692: Replay-guard bypass in ExportDKGResult

## Question
Can an unprivileged attacker use requestID, namespace, secret IDs, and encrypted-secret blobs at `POST /v2/vault/dkg_results/export` so `ExportDKGResult` treats a replayed or reformatted secrets request as fresh, leading to retrieve sensitive data/files from a running server such as blockchain keys or vault secrets and violating namespace, owner, and request-ID stamping must not change what was actually authorized?

## Target
- File/function: core/web/vault_controller.go::ExportDKGResult
- Entrypoint: POST /v2/vault/dkg_results/export
- Attacker controls: requestID, namespace, secret IDs, and encrypted-secret blobs
- Exploit idea: Replay and cross-owner vault requests with namespace collisions and signature edge cases to prove whether secret access stays bound to what was actually authorized.
- Invariant to test: namespace, owner, and request-ID stamping must not change what was actually authorized
- Expected Immunefi impact: retrieve sensitive data/files from a running server such as blockchain keys or vault secrets
- Fast validation: Send cross-owner namespace collisions, replayed IDs, and duplicate signer sets; assert the request is rejected before any secret read/write/delete side effect.
