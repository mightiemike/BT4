# Q1289: failure rollback leak in TransactionFactory.register

## Question
Can an unprivileged attacker use /wallet/broadcasttransaction to trigger a late failure after partial mutation in chainbase/src/main/java/org/tron/core/actuator/TransactionFactory.java::register, leaving pending or recent-transaction state changed while final settlement, receipts, or replay-protection state is rolled back or vice versa, and thereby causing Unauthorized or duplicate settlement via transaction-processing confusion?

## Target
- File/function: chainbase/src/main/java/org/tron/core/actuator/TransactionFactory.java::register
- Entrypoint: /wallet/broadcasttransaction
- Attacker controls: raw transaction bytes, signatures, tx ids, permission_id, retry order, duplicate broadcast timing, and visible/base58/hex fields
- Exploit idea: Force failures after the first ledger write, secondary index update, or reward/fee adjustment to see whether cleanup is asymmetric.
- Invariant to test: A failed broadcast, pending, receipt, or transaction-tracking flow must not leave surviving partial effects in pending or recent-transaction state or final settlement, receipts, or replay-protection state, except for the intended fee burn.
- Expected Immunefi impact: Unauthorized or duplicate settlement via transaction-processing confusion
- Fast validation: Inject values that fail after partial progress through /wallet/broadcasttransaction, then compare all touched ledgers and indexes against a clean pre-state snapshot.
