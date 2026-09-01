# Q1158: L1 fee reservation bypass via `inner_pool` (mempool.rs)

## Question
Can an unprivileged attacker who fills the pool with transactions sized at the block budget edge, controlling nonce, gas limit and `max_fee_per_gas`, drive `inner_pool` in `crates/sequencer/src/mempool.rs` so that the L1 fee `CitreaTransactionValidator` reserved and the L1 fee execution actually charges stop being equal, breaking the invariant that no transaction executes whose sender cannot pay the L1 fee?

## Target
- File/function: `crates/sequencer/src/mempool.rs` -> `inner_pool`
- Entrypoint: unprivileged party fills the pool with transactions sized at the block budget edge
- Attacker controls: nonce, gas limit and `max_fee_per_gas`
- Exploit idea: L1 fee reservation bypass - reach `inner_pool` from that entrypoint and force the divergence where the L1 fee `CitreaTransactionValidator` reserved and the L1 fee execution actually charges stop being equal; the adjacent symbols in the same file that carry the value are `CitreaMempool`, `add_external_transaction`, `get`, `all_transactions`, so evaluate both sides of the equality through them before and after the attacker's action.
- Invariant to test: no transaction executes whose sender cannot pay the L1 fee
- Expected Immunefi impact: High - transient consensus failure / unintended chain split recoverable only by resync
- Fast validation: submit a transaction at the balance boundary and assert execution neither underflows nor is skipped
