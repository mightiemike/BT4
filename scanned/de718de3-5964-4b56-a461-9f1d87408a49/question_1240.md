# Q1240: ShieldedTRC20ParametersBuilder: merkle tree unbounded work

## Question
Can an unprivileged attacker (shielded transaction) abuse `ShieldedTRC20ParametersBuilder.createSpendAuth` in `framework/src/main/java/org/tron/core/zen/ShieldedTRC20ParametersBuilder.java` — where the attacker forces ShieldedTRC20ParametersBuilder.createSpendAuth to build or walk an oversized merkle structure for cheap input — to break the invariant that tree operations in ShieldedTRC20ParametersBuilder.createSpendAuth are bounded by fee/energy, leading to: DoS via protocol implementation (Advanced)?

## Target
- File/function: `framework/src/main/java/org/tron/core/zen/ShieldedTRC20ParametersBuilder.java` -> `ShieldedTRC20ParametersBuilder.createSpendAuth`
- Entrypoint: shielded input to ShieldedTRC20ParametersBuilder.createSpendAuth maximizing tree work
- Attacker controls: request/transaction/contract inputs to `ShieldedTRC20ParametersBuilder.createSpendAuth` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: forces ShieldedTRC20ParametersBuilder.createSpendAuth to build or walk an oversized merkle structure for cheap input
- Invariant to test: tree operations in ShieldedTRC20ParametersBuilder.createSpendAuth are bounded by fee/energy
- Expected Immunefi impact: DoS via protocol implementation (Advanced)
- Fast validation: measure ShieldedTRC20ParametersBuilder.createSpendAuth work vs charged cost
