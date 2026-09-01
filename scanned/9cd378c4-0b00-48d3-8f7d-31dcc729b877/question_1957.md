# Q1957: L1 fee escape via `diff_size_send_eth_eoa` (handler.rs)

## Question
Can an unprivileged attacker who sends a transaction whose calldata maximises the computed L1 diff size, controlling value, gas and access list, drive `diff_size_send_eth_eoa` in `crates/evm/src/evm/handler.rs` so that the L1 diff size charged to the sender and the diff size the transaction actually contributes to the DA blob stop being equal, breaking the invariant that every user transaction pays for the L1 data it creates?

## Target
- File/function: `crates/evm/src/evm/handler.rs` -> `diff_size_send_eth_eoa`
- Entrypoint: unprivileged party sends a transaction whose calldata maximises the computed L1 diff size
- Attacker controls: value, gas and access list
- Exploit idea: L1 fee escape - reach `diff_size_send_eth_eoa` from that entrypoint and force the divergence where the L1 diff size charged to the sender and the diff size the transaction actually contributes to the DA blob stop being equal; the adjacent symbols in the same file that carry the value are `TxInfo`, `CitreaChainExt`, `CitreaChain`, `CitreaCallExt`, so evaluate both sides of the equality through them before and after the attacker's action.
- Invariant to test: every user transaction pays for the L1 data it creates
- Expected Immunefi impact: Critical - direct loss of user or vault funds
- Fast validation: maximise diff size with minimal charged size and diff `TxInfo::l1_diff_size` against the committed diff
