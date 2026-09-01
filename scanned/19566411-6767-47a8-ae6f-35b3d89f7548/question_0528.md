# Q0528: mempool admission versus block gas budget via `remove_transactions_by_sender` (mempool.rs)

## Question
Can an unprivileged attacker who submits a transaction whose balance sits exactly at the L1-fee reservation boundary, controlling calldata length and content, drive `remove_transactions_by_sender` in `crates/sequencer/src/mempool.rs` so that the transactions admitted and the transactions the block can actually fit stop being reconcilable, breaking the invariant that admission respects the block budget?

## Target
- File/function: `crates/sequencer/src/mempool.rs` -> `remove_transactions_by_sender`
- Entrypoint: unprivileged party submits a transaction whose balance sits exactly at the L1-fee reservation boundary
- Attacker controls: calldata length and content
- Exploit idea: mempool admission versus block gas budget - reach `remove_transactions_by_sender` from that entrypoint and force the divergence where the transactions admitted and the transactions the block can actually fit stop being reconcilable; the adjacent symbols in the same file that carry the value are `CitreaMempool`, `add_external_transaction`, `get`, `all_transactions`, so evaluate both sides of the equality through them before and after the attacker's action.
- Invariant to test: admission respects the block budget
- Expected Immunefi impact: Critical - network unable to confirm new transactions (settlement halt) triggered by attacker-shaped protocol data
- Fast validation: fill the pool at the budget edge and assert progress
