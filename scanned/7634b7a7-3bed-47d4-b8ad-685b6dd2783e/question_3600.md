# Q3600: ZenTransactionBuilder: merkle tree unbounded work

## Question
Can an unprivileged attacker (shielded transaction) abuse `ZenTransactionBuilder.buildWithoutAsk` in `framework/src/main/java/org/tron/core/zen/ZenTransactionBuilder.java` — where the attacker forces ZenTransactionBuilder.buildWithoutAsk to build or walk an oversized merkle structure for cheap input — to break the invariant that tree operations in ZenTransactionBuilder.buildWithoutAsk are bounded by fee/energy, leading to: DoS via protocol implementation (Advanced)?

## Target
- File/function: `framework/src/main/java/org/tron/core/zen/ZenTransactionBuilder.java` -> `ZenTransactionBuilder.buildWithoutAsk`
- Entrypoint: shielded input to ZenTransactionBuilder.buildWithoutAsk maximizing tree work
- Attacker controls: request/transaction/contract inputs to `ZenTransactionBuilder.buildWithoutAsk` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: forces ZenTransactionBuilder.buildWithoutAsk to build or walk an oversized merkle structure for cheap input
- Invariant to test: tree operations in ZenTransactionBuilder.buildWithoutAsk are bounded by fee/energy
- Expected Immunefi impact: DoS via protocol implementation (Advanced)
- Fast validation: measure ZenTransactionBuilder.buildWithoutAsk work vs charged cost
