# Q486: cache-eviction replay in TransactionUtil.getTransactionId

## Question
Can an unprivileged attacker exploit eviction or expiration around gRPC broadcastTransaction so actuator/src/main/java/org/tron/core/utils/TransactionUtil.java::getTransactionId forgets enough replay-protection, pending, or receipt state to accept the same logical action again and reach Replayed or double-applied transaction execution?

## Target
- File/function: actuator/src/main/java/org/tron/core/utils/TransactionUtil.java::getTransactionId
- Entrypoint: gRPC broadcastTransaction
- Attacker controls: raw transaction bytes, signatures, tx ids, permission_id, retry order, duplicate broadcast timing, and visible/base58/hex fields
- Exploit idea: Probe limits, expiration windows, restart behavior, and cache-vs-store disagreements for txs, filters, rewards, or note state.
- Invariant to test: Eviction or restart must never resurrect a completed public action or hide the durable result needed to reject replays.
- Expected Immunefi impact: Replayed or double-applied transaction execution
- Fast validation: Push the relevant cache to capacity or restart-equivalent states after one action via gRPC broadcastTransaction; assert duplicates still fail.
