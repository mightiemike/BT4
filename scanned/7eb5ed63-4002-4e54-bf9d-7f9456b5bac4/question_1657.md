# Q1657: L1 fee escape via `warm_addresses` (handler.rs)

## Question
Can an unprivileged attacker who sends a transaction that reverts after writing large state diffs, controlling revert timing inside the frame, drive `warm_addresses` in `crates/evm/src/evm/handler.rs` so that the L1 diff size charged to the sender and the diff size the transaction actually contributes to the DA blob stop being equal, breaking the invariant that every user transaction pays for the L1 data it creates?

## Target
- File/function: `crates/evm/src/evm/handler.rs` -> `warm_addresses`
- Entrypoint: unprivileged party sends a transaction that reverts after writing large state diffs
- Attacker controls: revert timing inside the frame
- Exploit idea: L1 fee escape - reach `warm_addresses` from that entrypoint and force the divergence where the L1 diff size charged to the sender and the diff size the transaction actually contributes to the DA blob stop being equal; the adjacent symbols in the same file that carry the value are `TxInfo`, `CitreaChainExt`, `CitreaChain`, `CitreaCallExt`, so evaluate both sides of the equality through them before and after the attacker's action.
- Invariant to test: every user transaction pays for the L1 data it creates
- Expected Immunefi impact: Critical - direct loss of user or vault funds
- Fast validation: maximise diff size with minimal charged size and diff `TxInfo::l1_diff_size` against the committed diff
