# Q5423: pending tag semantics via `get_transaction_receipt` (query.rs)

## Question
Can an unprivileged attacker who calls `eth_call` against an attacker-deployed contract at a historical block tag, controlling call overrides and state overrides, drive `get_transaction_receipt` in `crates/evm/src/query.rs` so that the state the `pending` tag exposes and the state the next block actually starts from stop being the same, breaking the invariant that the pending view never contradicts the block that follows?

## Target
- File/function: `crates/evm/src/query.rs` -> `get_transaction_receipt`
- Entrypoint: unprivileged party calls `eth_call` against an attacker-deployed contract at a historical block tag
- Attacker controls: call overrides and state overrides
- Exploit idea: pending tag semantics - reach `get_transaction_receipt` from that entrypoint and force the divergence where the state the `pending` tag exposes and the state the next block actually starts from stop being the same; the adjacent symbols in the same file that carry the value are `EstimatedTxExpenses`, `EstimatedDiffSize`, `gas_with_l1_overhead`, `l1_fee`, so evaluate both sides of the equality through them before and after the attacker's action.
- Invariant to test: the pending view never contradicts the block that follows
- Expected Immunefi impact: High - node serves state contradicting the proved chain to bridges, exchanges and Clementine operators
- Fast validation: read pending, seal the block, and diff
