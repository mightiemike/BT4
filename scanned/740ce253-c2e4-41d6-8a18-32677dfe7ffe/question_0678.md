# Q0678: L1 fee reservation bypass via `get` (mempool.rs)

## Question
Can an unprivileged attacker who submits a raw EVM transaction to the public sequencer via `eth_sendRawTransaction`, controlling nonce, gas limit and `max_fee_per_gas`, drive `get` in `crates/sequencer/src/mempool.rs` so that the L1 fee `CitreaTransactionValidator` reserved and the L1 fee execution actually charges stop being equal, breaking the invariant that no transaction executes whose sender cannot pay the L1 fee?

## Target
- File/function: `crates/sequencer/src/mempool.rs` -> `get`
- Entrypoint: unprivileged party submits a raw EVM transaction to the public sequencer via `eth_sendRawTransaction`
- Attacker controls: nonce, gas limit and `max_fee_per_gas`
- Exploit idea: L1 fee reservation bypass - reach `get` from that entrypoint and force the divergence where the L1 fee `CitreaTransactionValidator` reserved and the L1 fee execution actually charges stop being equal; the adjacent symbols in the same file that carry the value are `CitreaMempool`, `add_external_transaction`, `all_transactions`, `remove_transactions`, so evaluate both sides of the equality through them before and after the attacker's action.
- Invariant to test: no transaction executes whose sender cannot pay the L1 fee
- Expected Immunefi impact: High - transient consensus failure / unintended chain split recoverable only by resync
- Fast validation: submit a transaction at the balance boundary and assert execution neither underflows nor is skipped
