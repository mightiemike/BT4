# Q4635: call override crossing into real state via `get_block_receipts` (query.rs)

## Question
Can an unprivileged attacker who queries a slot it wrote at `pending`, `latest` and a block hash tag, controlling the storage slots its contract touches, drive `get_block_receipts` in `crates/evm/src/query.rs` so that the state an overridden `eth_call` mutates and the ephemeral overlay it is supposed to mutate stop being the same working set, breaking the invariant that no RPC call mutates persisted state?

## Target
- File/function: `crates/evm/src/query.rs` -> `get_block_receipts`
- Entrypoint: unprivileged party queries a slot it wrote at `pending`, `latest` and a block hash tag
- Attacker controls: the storage slots its contract touches
- Exploit idea: call override crossing into real state - reach `get_block_receipts` from that entrypoint and force the divergence where the state an overridden `eth_call` mutates and the ephemeral overlay it is supposed to mutate stop being the same working set; the adjacent symbols in the same file that carry the value are `EstimatedTxExpenses`, `EstimatedDiffSize`, `gas_with_l1_overhead`, `l1_fee`, so evaluate both sides of the equality through them before and after the attacker's action.
- Invariant to test: no RPC call mutates persisted state
- Expected Immunefi impact: High - unauthenticated RPC mutating node state or bypassing `Auth`
- Fast validation: run an override-heavy call and assert the state root is unchanged afterwards
