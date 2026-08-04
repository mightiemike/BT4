# Q1633: primary-index drift in TransactionRetCapsule.getData

## Question
Can an unprivileged attacker reach /jsonrpc eth_sendRawTransaction so chainbase/src/main/java/org/tron/core/capsule/TransactionRetCapsule.java::getData updates the primary representation of pending or recent-transaction state without the matching index or lifecycle view in final settlement, receipts, or replay-protection state, eventually causing Pending or receipt-state corruption that locks value or suppresses replay protection?

## Target
- File/function: chainbase/src/main/java/org/tron/core/capsule/TransactionRetCapsule.java::getData
- Entrypoint: /jsonrpc eth_sendRawTransaction
- Attacker controls: raw transaction bytes, signatures, tx ids, permission_id, retry order, duplicate broadcast timing, and visible/base58/hex fields
- Exploit idea: Trace flows that insert, delete, or rewrite the same logical object in more than one store, cache, or capsule.
- Invariant to test: Primary state and every corresponding index/cache must move together or a user must remain able to recover the asset cleanly.
- Expected Immunefi impact: Pending or receipt-state corruption that locks value or suppresses replay protection
- Fast validation: Exercise create/update/cancel/withdraw/replay sequences via /jsonrpc eth_sendRawTransaction, then diff primary records and index views after every step.
