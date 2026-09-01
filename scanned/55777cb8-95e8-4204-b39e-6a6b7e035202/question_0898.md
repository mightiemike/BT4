# Q0898: L1 fee reservation bypass via `on_new_head_block` (tx_validator.rs)

## Question
Can an unprivileged attacker who fills the pool with transactions sized at the block budget edge, controlling calldata length and content, drive `on_new_head_block` in `crates/sequencer/src/tx_validator.rs` so that the L1 fee `CitreaTransactionValidator` reserved and the L1 fee execution actually charges stop being equal, breaking the invariant that no transaction executes whose sender cannot pay the L1 fee?

## Target
- File/function: `crates/sequencer/src/tx_validator.rs` -> `on_new_head_block`
- Entrypoint: unprivileged party fills the pool with transactions sized at the block budget edge
- Attacker controls: calldata length and content
- Exploit idea: L1 fee reservation bypass - reach `on_new_head_block` from that entrypoint and force the divergence where the L1 fee `CitreaTransactionValidator` reserved and the L1 fee execution actually charges stop being equal; the adjacent symbols in the same file that carry the value are `CitreaTransactionValidator`, `validate_transaction`, so evaluate both sides of the equality through them before and after the attacker's action.
- Invariant to test: no transaction executes whose sender cannot pay the L1 fee
- Expected Immunefi impact: High - transient consensus failure / unintended chain split recoverable only by resync
- Fast validation: submit a transaction at the balance boundary and assert execution neither underflows nor is skipped
