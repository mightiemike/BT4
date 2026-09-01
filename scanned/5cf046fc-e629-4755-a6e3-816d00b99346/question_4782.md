# Q4782: state diff size non-determinism via `get_last_l1_height_in_light_client` (executor.rs)

## Question
Can an unprivileged attacker who chains nested frames that touch balances, gas refunds and access lists in one transaction, controlling calldata entropy, drive `get_last_l1_height_in_light_client` in `crates/evm/src/evm/executor.rs` so that the diff size computed natively and the diff size recomputed in the guest stop being equal, breaking the invariant that L1 fee inputs are deterministic across native and zk execution?

## Target
- File/function: `crates/evm/src/evm/executor.rs` -> `get_last_l1_height_in_light_client`
- Entrypoint: unprivileged party chains nested frames that touch balances, gas refunds and access lists in one transaction
- Attacker controls: calldata entropy
- Exploit idea: state diff size non-determinism - reach `get_last_l1_height_in_light_client` from that entrypoint and force the divergence where the diff size computed natively and the diff size recomputed in the guest stop being equal; the adjacent symbols in the same file that carry the value are `CitreaEvm`, `transact`, `commit`, `execute_multiple_tx`, so evaluate both sides of the equality through them before and after the attacker's action.
- Invariant to test: L1 fee inputs are deterministic across native and zk execution
- Expected Immunefi impact: Critical - unintended permanent chain split: honest nodes accept a state root the proved chain does not contain
- Fast validation: recompute the diff in a guest replay and compare
