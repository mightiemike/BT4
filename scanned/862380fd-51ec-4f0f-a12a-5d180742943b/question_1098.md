# Q1098: mempool admission versus block gas budget via `add_external_transaction` (mempool.rs)

## Question
Can an unprivileged attacker who submits a raw EVM transaction to the public sequencer via `eth_sendRawTransaction`, controlling calldata length and content, drive `add_external_transaction` in `crates/sequencer/src/mempool.rs` so that the transactions admitted and the transactions the block can actually fit stop being reconcilable, breaking the invariant that admission respects the block budget?

## Target
- File/function: `crates/sequencer/src/mempool.rs` -> `add_external_transaction`
- Entrypoint: unprivileged party submits a raw EVM transaction to the public sequencer via `eth_sendRawTransaction`
- Attacker controls: calldata length and content
- Exploit idea: mempool admission versus block gas budget - reach `add_external_transaction` from that entrypoint and force the divergence where the transactions admitted and the transactions the block can actually fit stop being reconcilable; the adjacent symbols in the same file that carry the value are `CitreaMempool`, `get`, `all_transactions`, `remove_transactions`, so evaluate both sides of the equality through them before and after the attacker's action.
- Invariant to test: admission respects the block budget
- Expected Immunefi impact: Critical - network unable to confirm new transactions (settlement halt) triggered by attacker-shaped protocol data
- Fast validation: fill the pool at the budget edge and assert progress
