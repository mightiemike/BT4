# Q0608: mempool admission versus block gas budget via `on_new_head_block` (tx_validator.rs)

## Question
Can an unprivileged attacker who submits transactions straddling a fork activation height, controlling calldata length and content, drive `on_new_head_block` in `crates/sequencer/src/tx_validator.rs` so that the transactions admitted and the transactions the block can actually fit stop being reconcilable, breaking the invariant that admission respects the block budget?

## Target
- File/function: `crates/sequencer/src/tx_validator.rs` -> `on_new_head_block`
- Entrypoint: unprivileged party submits transactions straddling a fork activation height
- Attacker controls: calldata length and content
- Exploit idea: mempool admission versus block gas budget - reach `on_new_head_block` from that entrypoint and force the divergence where the transactions admitted and the transactions the block can actually fit stop being reconcilable; the adjacent symbols in the same file that carry the value are `CitreaTransactionValidator`, `validate_transaction`, so evaluate both sides of the equality through them before and after the attacker's action.
- Invariant to test: admission respects the block budget
- Expected Immunefi impact: Critical - network unable to confirm new transactions (settlement halt) triggered by attacker-shaped protocol data
- Fast validation: fill the pool at the budget edge and assert progress
