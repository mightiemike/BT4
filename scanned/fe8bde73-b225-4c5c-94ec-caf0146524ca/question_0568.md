# Q0568: mempool admission versus block gas budget via `inner_pool` (mempool.rs)

## Question
Can an unprivileged attacker who submits a raw EVM transaction to the public sequencer via `eth_sendRawTransaction`, controlling account balance at submission time, drive `inner_pool` in `crates/sequencer/src/mempool.rs` so that the transactions admitted and the transactions the block can actually fit stop being reconcilable, breaking the invariant that admission respects the block budget?

## Target
- File/function: `crates/sequencer/src/mempool.rs` -> `inner_pool`
- Entrypoint: unprivileged party submits a raw EVM transaction to the public sequencer via `eth_sendRawTransaction`
- Attacker controls: account balance at submission time
- Exploit idea: mempool admission versus block gas budget - reach `inner_pool` from that entrypoint and force the divergence where the transactions admitted and the transactions the block can actually fit stop being reconcilable; the adjacent symbols in the same file that carry the value are `CitreaMempool`, `add_external_transaction`, `get`, `all_transactions`, so evaluate both sides of the equality through them before and after the attacker's action.
- Invariant to test: admission respects the block budget
- Expected Immunefi impact: Critical - network unable to confirm new transactions (settlement halt) triggered by attacker-shaped protocol data
- Fast validation: fill the pool at the budget edge and assert progress
