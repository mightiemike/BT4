# Q3172: witness under-constrains a read via `delete_singleton` (scratchpad.rs)

## Question
Can an unprivileged attacker who sends a transaction that reads state written earlier in the same block by another of its transactions, controlling intra-block ordering of its own transactions, drive `delete_singleton` in `crates/sovereign-sdk/module-system/sov-modules-core/src/storage/scratchpad.rs` so that the value the native execution read from storage and the value the guest pops from the witness stop being forced equal, breaking the invariant that every guest read is bound to the pre-state root?

## Target
- File/function: `crates/sovereign-sdk/module-system/sov-modules-core/src/storage/scratchpad.rs` -> `delete_singleton`
- Entrypoint: unprivileged party sends a transaction that reads state written earlier in the same block by another of its transactions
- Attacker controls: intra-block ordering of its own transactions
- Exploit idea: witness under-constrains a read - reach `delete_singleton` from that entrypoint and force the divergence where the value the native execution read from storage and the value the guest pops from the witness stop being forced equal; the adjacent symbols in the same file that carry the value are `StateReaderAndWriter`, `StateDelta`, `AccessoryDelta`, `OffchainDelta`, so evaluate both sides of the equality through them before and after the attacker's action.
- Invariant to test: every guest read is bound to the pre-state root
- Expected Immunefi impact: Critical - a batch/light-client proof accepted for a state transition that never happened (false claim proved)
- Fast validation: replay with a mutated witness value and assert verification fails
