# Q1671: snapshot-rollback drift in AssetUtil.hasAssetV2

## Question
Can an unprivileged attacker trigger gRPC createTransaction2 -> broadcastTransaction so chainbase/src/main/java/org/tron/core/capsule/utils/AssetUtil.java::hasAssetV2 rolls back one store view but leaves another advanced, separating sender or issuer balances from recipient balances, fee burn, or asset accounting and leading to Deterministic invalid state divergence or unauthorized partial settlement?

## Target
- File/function: chainbase/src/main/java/org/tron/core/capsule/utils/AssetUtil.java::hasAssetV2
- Entrypoint: gRPC createTransaction2 -> broadcastTransaction
- Attacker controls: owner/to addresses, amount, asset id, permission_id, signatures, and visible/base58/hex encoding
- Exploit idea: Focus on nested snapshots, revoking stores, and multi-store flows that cross account, order, note, reward, or receipt state.
- Invariant to test: Rollback must restore one coherent state across all touched stores and indexes for a failed public action.
- Expected Immunefi impact: Deterministic invalid state divergence or unauthorized partial settlement
- Fast validation: Force failures after each write point via gRPC createTransaction2 -> broadcastTransaction, then compare all affected stores to a pristine snapshot.
