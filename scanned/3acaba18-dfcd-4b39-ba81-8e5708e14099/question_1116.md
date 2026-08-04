# Q1116: log-trace side effect in InternalTransaction.getParentHash

## Question
Can an unprivileged attacker use /jsonrpc eth_sendRawTransaction so chainbase/src/main/java/org/tron/common/runtime/InternalTransaction.java::getParentHash emits logs, traces, or bloom updates that survive a failed branch or disagree with the committed pending or recent-transaction state/final settlement, receipts, or replay-protection state, enabling later settlement or monitoring logic to act on false execution state and causing Repeatable invalid settlement, false spent-state decisions, or node resource abuse from inconsistent execution artifacts?

## Target
- File/function: chainbase/src/main/java/org/tron/common/runtime/InternalTransaction.java::getParentHash
- Entrypoint: /jsonrpc eth_sendRawTransaction
- Attacker controls: raw transaction bytes, signatures, tx ids, permission_id, retry order, duplicate broadcast timing, and visible/base58/hex fields
- Exploit idea: Trigger logging before late failures, nested revert patterns, and edge cases where trace collection is decoupled from state commits.
- Invariant to test: Published execution artifacts must correspond exactly to the committed execution branch and final pending or recent-transaction state/final settlement, receipts, or replay-protection state.
- Expected Immunefi impact: Repeatable invalid settlement, false spent-state decisions, or node resource abuse from inconsistent execution artifacts
- Fast validation: Construct late-reverting contracts via /jsonrpc eth_sendRawTransaction; assert committed logs, traces, and blooms match only the surviving branch.
