# Q1619: witness under-constrains a read via `singleton` (mod.rs)

## Question
Can an unprivileged attacker who sends a transaction that reads state written earlier in the same block by another of its transactions, controlling the size and shape of the state diff, drive `singleton` in `crates/sovereign-sdk/module-system/sov-modules-core/src/storage/mod.rs` so that the value the native execution read from storage and the value the guest pops from the witness stop being forced equal, breaking the invariant that every guest read is bound to the pre-state root?

## Target
- File/function: `crates/sovereign-sdk/module-system/sov-modules-core/src/storage/mod.rs` -> `singleton`
- Entrypoint: unprivileged party sends a transaction that reads state written earlier in the same block by another of its transactions
- Attacker controls: the size and shape of the state diff
- Exploit idea: witness under-constrains a read - reach `singleton` from that entrypoint and force the divergence where the value the native execution read from storage and the value the guest pops from the witness stop being forced equal; the adjacent symbols in the same file that carry the value are `StorageKey`, `StorageValue`, `StorageProof`, `Storage`, so evaluate both sides of the equality through them before and after the attacker's action.
- Invariant to test: every guest read is bound to the pre-state root
- Expected Immunefi impact: Critical - a batch/light-client proof accepted for a state transition that never happened (false claim proved)
- Fast validation: replay with a mutated witness value and assert verification fails
