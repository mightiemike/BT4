# Q5315: estimateGas versus execution via `get_balance` (query.rs)

## Question
Can an unprivileged attacker who queries a slot it wrote at `pending`, `latest` and a block hash tag, controlling the storage slots its contract touches, drive `get_balance` in `crates/evm/src/query.rs` so that the gas `eth_estimateGas` reports and the gas the same call consumes on-chain stop being equal, breaking the invariant that estimation is an upper bound for the same state?

## Target
- File/function: `crates/evm/src/query.rs` -> `get_balance`
- Entrypoint: unprivileged party queries a slot it wrote at `pending`, `latest` and a block hash tag
- Attacker controls: the storage slots its contract touches
- Exploit idea: estimateGas versus execution - reach `get_balance` from that entrypoint and force the divergence where the gas `eth_estimateGas` reports and the gas the same call consumes on-chain stop being equal; the adjacent symbols in the same file that carry the value are `EstimatedTxExpenses`, `EstimatedDiffSize`, `gas_with_l1_overhead`, `l1_fee`, so evaluate both sides of the equality through them before and after the attacker's action.
- Invariant to test: estimation is an upper bound for the same state
- Expected Immunefi impact: High - node serves state contradicting the proved chain to bridges, exchanges and Clementine operators
- Fast validation: estimate then execute an L1-fee-heavy call and compare
