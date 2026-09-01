# Q3907: L1 fee escape via `get_last_l1_height_in_light_client` (executor.rs)

## Question
Can an unprivileged attacker who sends a transaction whose calldata maximises the computed L1 diff size, controlling calldata entropy, drive `get_last_l1_height_in_light_client` in `crates/evm/src/evm/executor.rs` so that the L1 diff size charged to the sender and the diff size the transaction actually contributes to the DA blob stop being equal, breaking the invariant that every user transaction pays for the L1 data it creates?

## Target
- File/function: `crates/evm/src/evm/executor.rs` -> `get_last_l1_height_in_light_client`
- Entrypoint: unprivileged party sends a transaction whose calldata maximises the computed L1 diff size
- Attacker controls: calldata entropy
- Exploit idea: L1 fee escape - reach `get_last_l1_height_in_light_client` from that entrypoint and force the divergence where the L1 diff size charged to the sender and the diff size the transaction actually contributes to the DA blob stop being equal; the adjacent symbols in the same file that carry the value are `CitreaEvm`, `transact`, `commit`, `execute_multiple_tx`, so evaluate both sides of the equality through them before and after the attacker's action.
- Invariant to test: every user transaction pays for the L1 data it creates
- Expected Immunefi impact: Critical - direct loss of user or vault funds
- Fast validation: maximise diff size with minimal charged size and diff `TxInfo::l1_diff_size` against the committed diff
