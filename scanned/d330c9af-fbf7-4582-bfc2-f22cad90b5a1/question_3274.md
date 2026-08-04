# Q3274: large-iteration underpricing in TxOutputUtil.newTxOutput

## Question
Can an unprivileged attacker use gRPC broadcastTransaction so framework/src/main/java/org/tron/core/capsule/utils/TxOutputUtil.java::newTxOutput performs large iterator walks, pagination scans, or reconstruction passes over transaction-processing state/the resulting accounting, receipt, or index state below true cost and reaches Materially underpriced public work?

## Target
- File/function: framework/src/main/java/org/tron/core/capsule/utils/TxOutputUtil.java::newTxOutput
- Entrypoint: gRPC broadcastTransaction
- Attacker controls: publicly supplied addresses, ids, amounts, signatures, indexes, and encoding flags
- Exploit idea: Target account-wide order lists, price indexes, reward histories, note windows, and block/transaction ranges that may require full-store traversal.
- Invariant to test: Publicly reachable iterations over stores and indexes must be bounded and proportionate to the cost or limits exposed to the attacker.
- Expected Immunefi impact: Materially underpriced public work
- Fast validation: Measure scan cost versus returned work for large but valid inputs via gRPC broadcastTransaction; flag any case with attacker-controlled superlinear or large-linear amplification.
