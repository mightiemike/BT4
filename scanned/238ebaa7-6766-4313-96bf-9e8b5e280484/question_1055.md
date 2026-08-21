# Q1055: ZenTransactionBuilder: merkle tree unbounded work

## Question
Can an unprivileged attacker (shielded transaction) abuse `ZenTransactionBuilder.createSpendAuth` in `framework/src/main/java/org/tron/core/zen/ZenTransactionBuilder.java` — where the attacker forces ZenTransactionBuilder.createSpendAuth to build or walk an oversized merkle structure for cheap input — to break the invariant that tree operations in ZenTransactionBuilder.createSpendAuth are bounded by fee/energy, leading to: DoS via protocol implementation (Advanced)?

## Target
- File/function: `framework/src/main/java/org/tron/core/zen/ZenTransactionBuilder.java` -> `ZenTransactionBuilder.createSpendAuth`
- Entrypoint: shielded input to ZenTransactionBuilder.createSpendAuth maximizing tree work
- Attacker controls: request/transaction/contract inputs to `ZenTransactionBuilder.createSpendAuth` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: forces ZenTransactionBuilder.createSpendAuth to build or walk an oversized merkle structure for cheap input
- Invariant to test: tree operations in ZenTransactionBuilder.createSpendAuth are bounded by fee/energy
- Expected Immunefi impact: DoS via protocol implementation (Advanced)
- Fast validation: measure ZenTransactionBuilder.createSpendAuth work vs charged cost
