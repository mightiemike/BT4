# Q3290: write-before-finality replay in PendingManager.close

## Question
Can an unprivileged attacker use /jsonrpc eth_sendRawTransaction so framework/src/main/java/org/tron/core/db/PendingManager.java::close records replay-protection or settlement state before final outcome is known, then exploit rollback or retry windows to get Replayed or double-applied transaction execution?

## Target
- File/function: framework/src/main/java/org/tron/core/db/PendingManager.java::close
- Entrypoint: /jsonrpc eth_sendRawTransaction
- Attacker controls: raw transaction bytes, signatures, tx ids, permission_id, retry order, duplicate broadcast timing, and visible/base58/hex fields
- Exploit idea: Look for writes that happen before the final success/failure decision and can later be retried through another public path.
- Invariant to test: Replay-protection and settlement markers must become durable exactly once and only after the final branch is known.
- Expected Immunefi impact: Replayed or double-applied transaction execution
- Fast validation: Inject failures after tentative writes via /jsonrpc eth_sendRawTransaction; assert retries cannot settle again or bypass replay protection.
