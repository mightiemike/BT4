# Q1922: write-before-finality replay in TransactionTrace.saveEnergyLeftOfOrigin

## Question
Can an unprivileged attacker use gRPC broadcastTransaction so chainbase/src/main/java/org/tron/core/db/TransactionTrace.java::saveEnergyLeftOfOrigin records replay-protection or settlement state before final outcome is known, then exploit rollback or retry windows to get Replayed or double-applied transaction execution?

## Target
- File/function: chainbase/src/main/java/org/tron/core/db/TransactionTrace.java::saveEnergyLeftOfOrigin
- Entrypoint: gRPC broadcastTransaction
- Attacker controls: raw transaction bytes, signatures, tx ids, permission_id, retry order, duplicate broadcast timing, and visible/base58/hex fields
- Exploit idea: Look for writes that happen before the final success/failure decision and can later be retried through another public path.
- Invariant to test: Replay-protection and settlement markers must become durable exactly once and only after the final branch is known.
- Expected Immunefi impact: Replayed or double-applied transaction execution
- Fast validation: Inject failures after tentative writes via gRPC broadcastTransaction; assert retries cannot settle again or bypass replay protection.
