# Q0070: L1 fee reservation bypass via `add_external_transaction` (mempool.rs)

## Question
Can an unprivileged attacker who submits transactions straddling a fork activation height, controlling account balance at submission time, drive `add_external_transaction` in `crates/sequencer/src/mempool.rs` so that the L1 fee `CitreaTransactionValidator` reserved and the L1 fee execution actually charges stop being equal, breaking the invariant that no transaction executes whose sender cannot pay the L1 fee?

## Target
- File/function: `crates/sequencer/src/mempool.rs` -> `add_external_transaction`
- Entrypoint: unprivileged party submits transactions straddling a fork activation height
- Attacker controls: account balance at submission time
- Exploit idea: L1 fee reservation bypass - reach `add_external_transaction` from that entrypoint and force the divergence where the L1 fee `CitreaTransactionValidator` reserved and the L1 fee execution actually charges stop being equal; the adjacent symbols in the same file that carry the value are `CitreaMempool`, `get`, `all_transactions`, `remove_transactions`, so evaluate both sides of the equality through them before and after the attacker's action.
- Invariant to test: no transaction executes whose sender cannot pay the L1 fee
- Expected Immunefi impact: High - transient consensus failure / unintended chain split recoverable only by resync
- Fast validation: submit a transaction at the balance boundary and assert execution neither underflows nor is skipped
