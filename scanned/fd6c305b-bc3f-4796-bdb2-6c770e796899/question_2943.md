# Q2943: pending tag semantics via `set_state_to_end_of_evm_block_by_block_id` (query.rs)

## Question
Can an unprivileged attacker who calls `eth_estimateGas` on a contract that reads block and L1 state, controlling the storage slots its contract touches, drive `set_state_to_end_of_evm_block_by_block_id` in `crates/evm/src/query.rs` so that the state the `pending` tag exposes and the state the next block actually starts from stop being the same, breaking the invariant that the pending view never contradicts the block that follows?

## Target
- File/function: `crates/evm/src/query.rs` -> `set_state_to_end_of_evm_block_by_block_id`
- Entrypoint: unprivileged party calls `eth_estimateGas` on a contract that reads block and L1 state
- Attacker controls: the storage slots its contract touches
- Exploit idea: pending tag semantics - reach `set_state_to_end_of_evm_block_by_block_id` from that entrypoint and force the divergence where the state the `pending` tag exposes and the state the next block actually starts from stop being the same; the adjacent symbols in the same file that carry the value are `EstimatedTxExpenses`, `EstimatedDiffSize`, `gas_with_l1_overhead`, `l1_fee`, so evaluate both sides of the equality through them before and after the attacker's action.
- Invariant to test: the pending view never contradicts the block that follows
- Expected Immunefi impact: High - node serves state contradicting the proved chain to bridges, exchanges and Clementine operators
- Fast validation: read pending, seal the block, and diff
