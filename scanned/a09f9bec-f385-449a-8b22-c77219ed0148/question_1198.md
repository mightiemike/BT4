# Q1198: mempool/state view skew via `on_new_head_block` (tx_validator.rs)

## Question
Can an unprivileged attacker who submits transactions straddling a fork activation height, controlling nonce, gas limit and `max_fee_per_gas`, drive `on_new_head_block` in `crates/sequencer/src/tx_validator.rs` so that the account state the validator simulated against and the state at execution stop being the same view, breaking the invariant that admission decisions are monotone with respect to the chain they are applied to?

## Target
- File/function: `crates/sequencer/src/tx_validator.rs` -> `on_new_head_block`
- Entrypoint: unprivileged party submits transactions straddling a fork activation height
- Attacker controls: nonce, gas limit and `max_fee_per_gas`
- Exploit idea: mempool/state view skew - reach `on_new_head_block` from that entrypoint and force the divergence where the account state the validator simulated against and the state at execution stop being the same view; the adjacent symbols in the same file that carry the value are `CitreaTransactionValidator`, `validate_transaction`, so evaluate both sides of the equality through them before and after the attacker's action.
- Invariant to test: admission decisions are monotone with respect to the chain they are applied to
- Expected Immunefi impact: High - transient consensus failure / unintended chain split recoverable only by resync
- Fast validation: simulate with a pending-state provider and assert no admitted transaction fails at execution
