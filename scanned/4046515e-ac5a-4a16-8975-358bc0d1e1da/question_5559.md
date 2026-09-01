# Q5559: estimateGas versus execution via `block_hash_from_number` (query.rs)

## Question
Can an unprivileged attacker who calls `eth_call` against an attacker-deployed contract at a historical block tag, controlling the storage slots its contract touches, drive `block_hash_from_number` in `crates/evm/src/query.rs` so that the gas `eth_estimateGas` reports and the gas the same call consumes on-chain stop being equal, breaking the invariant that estimation is an upper bound for the same state?

## Target
- File/function: `crates/evm/src/query.rs` -> `block_hash_from_number`
- Entrypoint: unprivileged party calls `eth_call` against an attacker-deployed contract at a historical block tag
- Attacker controls: the storage slots its contract touches
- Exploit idea: estimateGas versus execution - reach `block_hash_from_number` from that entrypoint and force the divergence where the gas `eth_estimateGas` reports and the gas the same call consumes on-chain stop being equal; the adjacent symbols in the same file that carry the value are `EstimatedTxExpenses`, `EstimatedDiffSize`, `gas_with_l1_overhead`, `l1_fee`, so evaluate both sides of the equality through them before and after the attacker's action.
- Invariant to test: estimation is an upper bound for the same state
- Expected Immunefi impact: High - node serves state contradicting the proved chain to bridges, exchanges and Clementine operators
- Fast validation: estimate then execute an L1-fee-heavy call and compare
