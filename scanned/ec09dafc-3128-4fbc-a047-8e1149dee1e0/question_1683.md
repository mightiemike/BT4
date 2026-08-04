# Q1683: snapshot-rollback drift in BlockUtil.newGenesisBlockCapsule

## Question
Can an unprivileged attacker trigger /wallet/broadcasthex so chainbase/src/main/java/org/tron/core/capsule/utils/BlockUtil.java::newGenesisBlockCapsule rolls back one store view but leaves another advanced, separating transaction-processing state from the resulting accounting, receipt, or index state and leading to Deterministic invalid state divergence or unauthorized partial settlement?

## Target
- File/function: chainbase/src/main/java/org/tron/core/capsule/utils/BlockUtil.java::newGenesisBlockCapsule
- Entrypoint: /wallet/broadcasthex
- Attacker controls: publicly supplied addresses, ids, amounts, signatures, indexes, and encoding flags
- Exploit idea: Focus on nested snapshots, revoking stores, and multi-store flows that cross account, order, note, reward, or receipt state.
- Invariant to test: Rollback must restore one coherent state across all touched stores and indexes for a failed public action.
- Expected Immunefi impact: Deterministic invalid state divergence or unauthorized partial settlement
- Fast validation: Force failures after each write point via /wallet/broadcasthex, then compare all affected stores to a pristine snapshot.
