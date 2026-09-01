# Q0598: admission on a stale fork via `on_new_head_block` (tx_validator.rs)

## Question
Can an unprivileged attacker who fills the pool with transactions sized at the block budget edge, controlling nonce, gas limit and `max_fee_per_gas`, drive `on_new_head_block` in `crates/sequencer/src/tx_validator.rs` so that the fork rules the validator applied and the fork rules in force at inclusion stop being the same rules, breaking the invariant that admission and execution agree on the active fork?

## Target
- File/function: `crates/sequencer/src/tx_validator.rs` -> `on_new_head_block`
- Entrypoint: unprivileged party fills the pool with transactions sized at the block budget edge
- Attacker controls: nonce, gas limit and `max_fee_per_gas`
- Exploit idea: admission on a stale fork - reach `on_new_head_block` from that entrypoint and force the divergence where the fork rules the validator applied and the fork rules in force at inclusion stop being the same rules; the adjacent symbols in the same file that carry the value are `CitreaTransactionValidator`, `validate_transaction`, so evaluate both sides of the equality through them before and after the attacker's action.
- Invariant to test: admission and execution agree on the active fork
- Expected Immunefi impact: High - transient consensus failure / unintended chain split recoverable only by resync
- Fast validation: straddle an activation height and assert no admitted transaction becomes invalid
