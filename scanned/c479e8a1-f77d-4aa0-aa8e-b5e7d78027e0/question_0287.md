# Q0287: receipt root / log bloom construction via `transact` (executor.rs)

## Question
Can an unprivileged attacker who deploys a contract and calls it in the same L2 block, controlling value, gas and access list, drive `transact` in `crates/evm/src/evm/executor.rs` so that the receipt root the header commits and the root recomputed from the executed receipts stop being equal, breaking the invariant that the header commits exactly the executed receipts?

## Target
- File/function: `crates/evm/src/evm/executor.rs` -> `transact`
- Entrypoint: unprivileged party deploys a contract and calls it in the same L2 block
- Attacker controls: value, gas and access list
- Exploit idea: receipt root / log bloom construction - reach `transact` from that entrypoint and force the divergence where the receipt root the header commits and the root recomputed from the executed receipts stop being equal; the adjacent symbols in the same file that carry the value are `CitreaEvm`, `commit`, `execute_multiple_tx`, `verify_system_tx`, so evaluate both sides of the equality through them before and after the attacker's action.
- Invariant to test: the header commits exactly the executed receipts
- Expected Immunefi impact: Critical - unintended permanent chain split: honest nodes accept a state root the proved chain does not contain
- Fast validation: recompute receipts for an adversarial block and diff
