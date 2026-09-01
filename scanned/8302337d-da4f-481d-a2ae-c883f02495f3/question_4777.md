# Q4777: gas refund/limit interaction with L1 fee via `get_last_l1_height_in_light_client` (executor.rs)

## Question
Can an unprivileged attacker who sends a transaction whose calldata maximises the computed L1 diff size, controlling contract bytecode and calldata, drive `get_last_l1_height_in_light_client` in `crates/evm/src/evm/executor.rs` so that the effective gas price used for refunds and the price used for the L1 fee charge stop being consistent, breaking the invariant that refunds never return more than was paid?

## Target
- File/function: `crates/evm/src/evm/executor.rs` -> `get_last_l1_height_in_light_client`
- Entrypoint: unprivileged party sends a transaction whose calldata maximises the computed L1 diff size
- Attacker controls: contract bytecode and calldata
- Exploit idea: gas refund/limit interaction with L1 fee - reach `get_last_l1_height_in_light_client` from that entrypoint and force the divergence where the effective gas price used for refunds and the price used for the L1 fee charge stop being consistent; the adjacent symbols in the same file that carry the value are `CitreaEvm`, `transact`, `commit`, `execute_multiple_tx`, so evaluate both sides of the equality through them before and after the attacker's action.
- Invariant to test: refunds never return more than was paid
- Expected Immunefi impact: Critical - direct loss of user or vault funds
- Fast validation: maximise refunds under an L1-fee-heavy transaction and assert non-negative net
