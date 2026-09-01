# Q5167: receipt root / log bloom construction via `verify_system_tx` (executor.rs)

## Question
Can an unprivileged attacker who chains nested frames that touch balances, gas refunds and access lists in one transaction, controlling revert timing inside the frame, drive `verify_system_tx` in `crates/evm/src/evm/executor.rs` so that the receipt root the header commits and the root recomputed from the executed receipts stop being equal, breaking the invariant that the header commits exactly the executed receipts?

## Target
- File/function: `crates/evm/src/evm/executor.rs` -> `verify_system_tx`
- Entrypoint: unprivileged party chains nested frames that touch balances, gas refunds and access lists in one transaction
- Attacker controls: revert timing inside the frame
- Exploit idea: receipt root / log bloom construction - reach `verify_system_tx` from that entrypoint and force the divergence where the receipt root the header commits and the root recomputed from the executed receipts stop being equal; the adjacent symbols in the same file that carry the value are `CitreaEvm`, `transact`, `commit`, `execute_multiple_tx`, so evaluate both sides of the equality through them before and after the attacker's action.
- Invariant to test: the header commits exactly the executed receipts
- Expected Immunefi impact: Critical - unintended permanent chain split: honest nodes accept a state root the proved chain does not contain
- Fast validation: recompute receipts for an adversarial block and diff
