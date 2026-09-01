# Q5478: read/write log ordering via `with_witness_and_log` (scratchpad.rs)

## Question
Can an unprivileged attacker who sends a transaction that reads state written earlier in the same block by another of its transactions, controlling the size and shape of the state diff, drive `with_witness_and_log` in `crates/sovereign-sdk/module-system/sov-modules-core/src/storage/scratchpad.rs` so that the `ReadWriteLog` order the native run produced and the order the guest consumes stop being the same order, breaking the invariant that log ordering is canonical and replay-stable?

## Target
- File/function: `crates/sovereign-sdk/module-system/sov-modules-core/src/storage/scratchpad.rs` -> `with_witness_and_log`
- Entrypoint: unprivileged party sends a transaction that reads state written earlier in the same block by another of its transactions
- Attacker controls: the size and shape of the state diff
- Exploit idea: read/write log ordering - reach `with_witness_and_log` from that entrypoint and force the divergence where the `ReadWriteLog` order the native run produced and the order the guest consumes stop being the same order; the adjacent symbols in the same file that carry the value are `StateReaderAndWriter`, `StateDelta`, `AccessoryDelta`, `OffchainDelta`, so evaluate both sides of the equality through them before and after the attacker's action.
- Invariant to test: log ordering is canonical and replay-stable
- Expected Immunefi impact: Critical - unintended permanent chain split: honest nodes accept a state root the proved chain does not contain
- Fast validation: shuffle log order in a replay and assert rejection
