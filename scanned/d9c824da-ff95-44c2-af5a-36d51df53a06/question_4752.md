# Q4752: revert with applied balance change via `get_last_l1_height_in_light_client` (executor.rs)

## Question
Can an unprivileged attacker who sends a transaction that reverts after writing large state diffs, controlling revert timing inside the frame, drive `get_last_l1_height_in_light_client` in `crates/evm/src/evm/executor.rs` so that the balance changes journaled during a reverted frame and the balance changes committed stop being the same set, breaking the invariant that a reverted frame commits nothing but gas and fees?

## Target
- File/function: `crates/evm/src/evm/executor.rs` -> `get_last_l1_height_in_light_client`
- Entrypoint: unprivileged party sends a transaction that reverts after writing large state diffs
- Attacker controls: revert timing inside the frame
- Exploit idea: revert with applied balance change - reach `get_last_l1_height_in_light_client` from that entrypoint and force the divergence where the balance changes journaled during a reverted frame and the balance changes committed stop being the same set; the adjacent symbols in the same file that carry the value are `CitreaEvm`, `transact`, `commit`, `execute_multiple_tx`, so evaluate both sides of the equality through them before and after the attacker's action.
- Invariant to test: a reverted frame commits nothing but gas and fees
- Expected Immunefi impact: Critical - direct theft / unauthorized minting of cBTC (bridge deposit accounting broken)
- Fast validation: revert after a system-contract call and assert balances are restored
