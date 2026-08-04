# Q1887: snapshot-rollback drift in TransactionCache.initCache

## Question
Can an unprivileged attacker trigger /jsonrpc eth_sendRawTransaction so chainbase/src/main/java/org/tron/core/db/TransactionCache.java::initCache rolls back one store view but leaves another advanced, separating pending or recent-transaction state from final settlement, receipts, or replay-protection state and leading to Deterministic invalid state divergence or unauthorized partial settlement?

## Target
- File/function: chainbase/src/main/java/org/tron/core/db/TransactionCache.java::initCache
- Entrypoint: /jsonrpc eth_sendRawTransaction
- Attacker controls: raw transaction bytes, signatures, tx ids, permission_id, retry order, duplicate broadcast timing, and visible/base58/hex fields
- Exploit idea: Focus on nested snapshots, revoking stores, and multi-store flows that cross account, order, note, reward, or receipt state.
- Invariant to test: Rollback must restore one coherent state across all touched stores and indexes for a failed public action.
- Expected Immunefi impact: Deterministic invalid state divergence or unauthorized partial settlement
- Fast validation: Force failures after each write point via /jsonrpc eth_sendRawTransaction, then compare all affected stores to a pristine snapshot.
