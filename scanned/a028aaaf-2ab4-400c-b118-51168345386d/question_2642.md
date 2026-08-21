# Q2642: ZenTransactionBuilder: merkle tree unbounded work

## Question
Can an unprivileged attacker (shielded transaction) abuse `ZenTransactionBuilder.generateOutputProof` in `framework/src/main/java/org/tron/core/zen/ZenTransactionBuilder.java` — where the attacker forces ZenTransactionBuilder.generateOutputProof to build or walk an oversized merkle structure for cheap input — to break the invariant that tree operations in ZenTransactionBuilder.generateOutputProof are bounded by fee/energy, leading to: DoS via protocol implementation (Advanced)?

## Target
- File/function: `framework/src/main/java/org/tron/core/zen/ZenTransactionBuilder.java` -> `ZenTransactionBuilder.generateOutputProof`
- Entrypoint: shielded input to ZenTransactionBuilder.generateOutputProof maximizing tree work
- Attacker controls: request/transaction/contract inputs to `ZenTransactionBuilder.generateOutputProof` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: forces ZenTransactionBuilder.generateOutputProof to build or walk an oversized merkle structure for cheap input
- Invariant to test: tree operations in ZenTransactionBuilder.generateOutputProof are bounded by fee/energy
- Expected Immunefi impact: DoS via protocol implementation (Advanced)
- Fast validation: measure ZenTransactionBuilder.generateOutputProof work vs charged cost
