# Q0908: admission on a stale fork via `validate_transaction` (tx_validator.rs)

## Question
Can an unprivileged attacker who submits transactions straddling a fork activation height, controlling calldata length and content, drive `validate_transaction` in `crates/sequencer/src/tx_validator.rs` so that the fork rules the validator applied and the fork rules in force at inclusion stop being the same rules, breaking the invariant that admission and execution agree on the active fork?

## Target
- File/function: `crates/sequencer/src/tx_validator.rs` -> `validate_transaction`
- Entrypoint: unprivileged party submits transactions straddling a fork activation height
- Attacker controls: calldata length and content
- Exploit idea: admission on a stale fork - reach `validate_transaction` from that entrypoint and force the divergence where the fork rules the validator applied and the fork rules in force at inclusion stop being the same rules; the adjacent symbols in the same file that carry the value are `CitreaTransactionValidator`, `on_new_head_block`, so evaluate both sides of the equality through them before and after the attacker's action.
- Invariant to test: admission and execution agree on the active fork
- Expected Immunefi impact: High - transient consensus failure / unintended chain split recoverable only by resync
- Fast validation: straddle an activation height and assert no admitted transaction becomes invalid
