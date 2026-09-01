# Q0518: admission on a stale fork via `remove_transactions_by_sender` (mempool.rs)

## Question
Can an unprivileged attacker who fills the pool with transactions sized at the block budget edge, controlling the fork boundary it targets, drive `remove_transactions_by_sender` in `crates/sequencer/src/mempool.rs` so that the fork rules the validator applied and the fork rules in force at inclusion stop being the same rules, breaking the invariant that admission and execution agree on the active fork?

## Target
- File/function: `crates/sequencer/src/mempool.rs` -> `remove_transactions_by_sender`
- Entrypoint: unprivileged party fills the pool with transactions sized at the block budget edge
- Attacker controls: the fork boundary it targets
- Exploit idea: admission on a stale fork - reach `remove_transactions_by_sender` from that entrypoint and force the divergence where the fork rules the validator applied and the fork rules in force at inclusion stop being the same rules; the adjacent symbols in the same file that carry the value are `CitreaMempool`, `add_external_transaction`, `get`, `all_transactions`, so evaluate both sides of the equality through them before and after the attacker's action.
- Invariant to test: admission and execution agree on the active fork
- Expected Immunefi impact: High - transient consensus failure / unintended chain split recoverable only by resync
- Fast validation: straddle an activation height and assert no admitted transaction becomes invalid
