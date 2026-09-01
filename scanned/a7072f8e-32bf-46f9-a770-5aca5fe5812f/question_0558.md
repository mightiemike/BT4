# Q0558: admission on a stale fork via `inner_pool` (mempool.rs)

## Question
Can an unprivileged attacker who submits transactions straddling a fork activation height, controlling the fork boundary it targets, drive `inner_pool` in `crates/sequencer/src/mempool.rs` so that the fork rules the validator applied and the fork rules in force at inclusion stop being the same rules, breaking the invariant that admission and execution agree on the active fork?

## Target
- File/function: `crates/sequencer/src/mempool.rs` -> `inner_pool`
- Entrypoint: unprivileged party submits transactions straddling a fork activation height
- Attacker controls: the fork boundary it targets
- Exploit idea: admission on a stale fork - reach `inner_pool` from that entrypoint and force the divergence where the fork rules the validator applied and the fork rules in force at inclusion stop being the same rules; the adjacent symbols in the same file that carry the value are `CitreaMempool`, `add_external_transaction`, `get`, `all_transactions`, so evaluate both sides of the equality through them before and after the attacker's action.
- Invariant to test: admission and execution agree on the active fork
- Expected Immunefi impact: High - transient consensus failure / unintended chain split recoverable only by resync
- Fast validation: straddle an activation height and assert no admitted transaction becomes invalid
