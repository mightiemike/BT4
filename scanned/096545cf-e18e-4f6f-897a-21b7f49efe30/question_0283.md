# Q283: ZenTransactionBuilder: merkle tree unbounded work

## Question
Can an unprivileged attacker (shielded transaction) abuse `ZenTransactionBuilder.addOutput` in `framework/src/main/java/org/tron/core/zen/ZenTransactionBuilder.java` — where the attacker forces ZenTransactionBuilder.addOutput to build or walk an oversized merkle structure for cheap input — to break the invariant that tree operations in ZenTransactionBuilder.addOutput are bounded by fee/energy, leading to: DoS via protocol implementation (Advanced)?

## Target
- File/function: `framework/src/main/java/org/tron/core/zen/ZenTransactionBuilder.java` -> `ZenTransactionBuilder.addOutput`
- Entrypoint: shielded input to ZenTransactionBuilder.addOutput maximizing tree work
- Attacker controls: request/transaction/contract inputs to `ZenTransactionBuilder.addOutput` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: forces ZenTransactionBuilder.addOutput to build or walk an oversized merkle structure for cheap input
- Invariant to test: tree operations in ZenTransactionBuilder.addOutput are bounded by fee/energy
- Expected Immunefi impact: DoS via protocol implementation (Advanced)
- Fast validation: measure ZenTransactionBuilder.addOutput work vs charged cost
