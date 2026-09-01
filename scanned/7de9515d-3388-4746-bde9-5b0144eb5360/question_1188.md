# Q1188: mempool admission versus block gas budget via `remove_transactions_and_descendants` (mempool.rs)

## Question
Can an unprivileged attacker who submits transactions straddling a fork activation height, controlling nonce, gas limit and `max_fee_per_gas`, drive `remove_transactions_and_descendants` in `crates/sequencer/src/mempool.rs` so that the transactions admitted and the transactions the block can actually fit stop being reconcilable, breaking the invariant that admission respects the block budget?

## Target
- File/function: `crates/sequencer/src/mempool.rs` -> `remove_transactions_and_descendants`
- Entrypoint: unprivileged party submits transactions straddling a fork activation height
- Attacker controls: nonce, gas limit and `max_fee_per_gas`
- Exploit idea: mempool admission versus block gas budget - reach `remove_transactions_and_descendants` from that entrypoint and force the divergence where the transactions admitted and the transactions the block can actually fit stop being reconcilable; the adjacent symbols in the same file that carry the value are `CitreaMempool`, `add_external_transaction`, `get`, `all_transactions`, so evaluate both sides of the equality through them before and after the attacker's action.
- Invariant to test: admission respects the block budget
- Expected Immunefi impact: Critical - network unable to confirm new transactions (settlement halt) triggered by attacker-shaped protocol data
- Fast validation: fill the pool at the budget edge and assert progress
