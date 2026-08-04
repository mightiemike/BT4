# Q1157: failure rollback leak in WalletUtil.checkPermissionOperations

## Question
Can an unprivileged attacker use gRPC broadcastTransaction to trigger a late failure after partial mutation in chainbase/src/main/java/org/tron/common/utils/WalletUtil.java::checkPermissionOperations, leaving transaction-processing state changed while the resulting accounting, receipt, or index state is rolled back or vice versa, and thereby causing Unauthorized transaction execution or state mutation?

## Target
- File/function: chainbase/src/main/java/org/tron/common/utils/WalletUtil.java::checkPermissionOperations
- Entrypoint: gRPC broadcastTransaction
- Attacker controls: publicly supplied addresses, ids, amounts, signatures, indexes, and encoding flags
- Exploit idea: Force failures after the first ledger write, secondary index update, or reward/fee adjustment to see whether cleanup is asymmetric.
- Invariant to test: A failed public transaction-processing flow must not leave surviving partial effects in transaction-processing state or the resulting accounting, receipt, or index state, except for the intended fee burn.
- Expected Immunefi impact: Unauthorized transaction execution or state mutation
- Fast validation: Inject values that fail after partial progress through gRPC broadcastTransaction, then compare all touched ledgers and indexes against a clean pre-state snapshot.
