# Q1551: snapshot-rollback drift in ProposalCapsule.hasProcessed

## Question
Can an unprivileged attacker trigger /wallet/updateaccount -> sign -> /wallet/broadcasttransaction so chainbase/src/main/java/org/tron/core/capsule/ProposalCapsule.java::hasProcessed rolls back one store view but leaves another advanced, separating the account permission tree or contract-owner binding from the effective sign weight or authorized operation set and leading to Deterministic invalid state divergence or unauthorized partial settlement?

## Target
- File/function: chainbase/src/main/java/org/tron/core/capsule/ProposalCapsule.java::hasProcessed
- Entrypoint: /wallet/updateaccount -> sign -> /wallet/broadcasttransaction
- Attacker controls: owner address, permission_id, threshold, signer weights, operations mask, contract address, and signatures
- Exploit idea: Focus on nested snapshots, revoking stores, and multi-store flows that cross account, order, note, reward, or receipt state.
- Invariant to test: Rollback must restore one coherent state across all touched stores and indexes for a failed public action.
- Expected Immunefi impact: Deterministic invalid state divergence or unauthorized partial settlement
- Fast validation: Force failures after each write point via /wallet/updateaccount -> sign -> /wallet/broadcasttransaction, then compare all affected stores to a pristine snapshot.
