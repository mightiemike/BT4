# Q3167: query executed against a pruned root via `simulate_tx_l1_fee` (query.rs)

## Question
Can an unprivileged attacker who calls `eth_estimateGas` on a contract that reads block and L1 state, controlling the storage slots its contract touches, drive `simulate_tx_l1_fee` in `crates/evm/src/query.rs` so that the root a query executes against and a root the node can still prove stop being the same, breaking the invariant that queries never answer from unprovable state?

## Target
- File/function: `crates/evm/src/query.rs` -> `simulate_tx_l1_fee`
- Entrypoint: unprivileged party calls `eth_estimateGas` on a contract that reads block and L1 state
- Attacker controls: the storage slots its contract touches
- Exploit idea: query executed against a pruned root - reach `simulate_tx_l1_fee` from that entrypoint and force the divergence where the root a query executes against and a root the node can still prove stop being the same; the adjacent symbols in the same file that carry the value are `EstimatedTxExpenses`, `EstimatedDiffSize`, `gas_with_l1_overhead`, `l1_fee`, so evaluate both sides of the equality through them before and after the attacker's action.
- Invariant to test: queries never answer from unprovable state
- Expected Immunefi impact: High - node serves state contradicting the proved chain to bridges, exchanges and Clementine operators
- Fast validation: query below the prune horizon and assert an explicit error
