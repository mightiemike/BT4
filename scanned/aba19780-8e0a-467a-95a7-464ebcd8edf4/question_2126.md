# Q2126: write-before-finality replay in AccountIndexStore.put

## Question
Can an unprivileged attacker use gRPC createTransaction2 -> broadcastTransaction so chainbase/src/main/java/org/tron/core/store/AccountIndexStore.java::put records replay-protection or settlement state before final outcome is known, then exploit rollback or retry windows to get Double settlement of one transfer or asset move?

## Target
- File/function: chainbase/src/main/java/org/tron/core/store/AccountIndexStore.java::put
- Entrypoint: gRPC createTransaction2 -> broadcastTransaction
- Attacker controls: owner/to addresses, amount, asset id, permission_id, signatures, and visible/base58/hex encoding
- Exploit idea: Look for writes that happen before the final success/failure decision and can later be retried through another public path.
- Invariant to test: Replay-protection and settlement markers must become durable exactly once and only after the final branch is known.
- Expected Immunefi impact: Double settlement of one transfer or asset move
- Fast validation: Inject failures after tentative writes via gRPC createTransaction2 -> broadcastTransaction; assert retries cannot settle again or bypass replay protection.
