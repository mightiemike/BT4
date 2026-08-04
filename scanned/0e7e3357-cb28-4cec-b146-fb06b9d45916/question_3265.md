# Q3265: primary-index drift in TxOutputUtil.newTxOutput

## Question
Can an unprivileged attacker reach gRPC broadcastTransaction so framework/src/main/java/org/tron/core/capsule/utils/TxOutputUtil.java::newTxOutput updates the primary representation of transaction-processing state without the matching index or lifecycle view in the resulting accounting, receipt, or index state, eventually causing Permanent lock or stale-state corruption?

## Target
- File/function: framework/src/main/java/org/tron/core/capsule/utils/TxOutputUtil.java::newTxOutput
- Entrypoint: gRPC broadcastTransaction
- Attacker controls: publicly supplied addresses, ids, amounts, signatures, indexes, and encoding flags
- Exploit idea: Trace flows that insert, delete, or rewrite the same logical object in more than one store, cache, or capsule.
- Invariant to test: Primary state and every corresponding index/cache must move together or a user must remain able to recover the asset cleanly.
- Expected Immunefi impact: Permanent lock or stale-state corruption
- Fast validation: Exercise create/update/cancel/withdraw/replay sequences via gRPC broadcastTransaction, then diff primary records and index views after every step.
