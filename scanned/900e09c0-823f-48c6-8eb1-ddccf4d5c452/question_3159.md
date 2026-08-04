# Q3159: snapshot-rollback drift in SpendDescriptionCapsule.getValueCommitment

## Question
Can an unprivileged attacker trigger gRPC broadcastTransaction so framework/src/main/java/org/tron/core/capsule/SpendDescriptionCapsule.java::getValueCommitment rolls back one store view but leaves another advanced, separating transaction-processing state from the resulting accounting, receipt, or index state and leading to Deterministic invalid state divergence or unauthorized partial settlement?

## Target
- File/function: framework/src/main/java/org/tron/core/capsule/SpendDescriptionCapsule.java::getValueCommitment
- Entrypoint: gRPC broadcastTransaction
- Attacker controls: publicly supplied addresses, ids, amounts, signatures, indexes, and encoding flags
- Exploit idea: Focus on nested snapshots, revoking stores, and multi-store flows that cross account, order, note, reward, or receipt state.
- Invariant to test: Rollback must restore one coherent state across all touched stores and indexes for a failed public action.
- Expected Immunefi impact: Deterministic invalid state divergence or unauthorized partial settlement
- Fast validation: Force failures after each write point via gRPC broadcastTransaction, then compare all affected stores to a pristine snapshot.
