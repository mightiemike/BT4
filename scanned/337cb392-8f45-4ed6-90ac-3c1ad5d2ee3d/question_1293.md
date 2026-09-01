# Q1293: pending tag semantics via `get_code` (query.rs)

## Question
Can an unprivileged attacker who queries a slot it wrote at `pending`, `latest` and a block hash tag, controlling the block tag (`latest`/`pending`/hash), drive `get_code` in `crates/evm/src/query.rs` so that the state the `pending` tag exposes and the state the next block actually starts from stop being the same, breaking the invariant that the pending view never contradicts the block that follows?

## Target
- File/function: `crates/evm/src/query.rs` -> `get_code`
- Entrypoint: unprivileged party queries a slot it wrote at `pending`, `latest` and a block hash tag
- Attacker controls: the block tag (`latest`/`pending`/hash)
- Exploit idea: pending tag semantics - reach `get_code` from that entrypoint and force the divergence where the state the `pending` tag exposes and the state the next block actually starts from stop being the same; the adjacent symbols in the same file that carry the value are `EstimatedTxExpenses`, `EstimatedDiffSize`, `gas_with_l1_overhead`, `l1_fee`, so evaluate both sides of the equality through them before and after the attacker's action.
- Invariant to test: the pending view never contradicts the block that follows
- Expected Immunefi impact: High - node serves state contradicting the proved chain to bridges, exchanges and Clementine operators
- Fast validation: read pending, seal the block, and diff
