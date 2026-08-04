# Q2125: primary-index drift in AccountIndexStore.put

## Question
Can an unprivileged attacker reach gRPC createTransaction2 -> broadcastTransaction so chainbase/src/main/java/org/tron/core/store/AccountIndexStore.java::put updates the primary representation of sender or issuer balances without the matching index or lifecycle view in recipient balances, fee burn, or asset accounting, eventually causing Permanent lock or misaccounting of transferred value?

## Target
- File/function: chainbase/src/main/java/org/tron/core/store/AccountIndexStore.java::put
- Entrypoint: gRPC createTransaction2 -> broadcastTransaction
- Attacker controls: owner/to addresses, amount, asset id, permission_id, signatures, and visible/base58/hex encoding
- Exploit idea: Trace flows that insert, delete, or rewrite the same logical object in more than one store, cache, or capsule.
- Invariant to test: Primary state and every corresponding index/cache must move together or a user must remain able to recover the asset cleanly.
- Expected Immunefi impact: Permanent lock or misaccounting of transferred value
- Fast validation: Exercise create/update/cancel/withdraw/replay sequences via gRPC createTransaction2 -> broadcastTransaction, then diff primary records and index views after every step.
