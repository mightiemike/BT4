# Q2553: program_cache_entry::load - builtin entry constructed for a user program (upgrading the program twice within one)

## Question
Can an unprivileged attacker who deploys and invokes its own programs whose cache entries carry verification and tombstone state, upgrading the program twice within one block, drive `program_cache_entry::load` to get new_builtin used for an attacker-deployed program so it bypasses verification and metering, so that the invariant that builtin entries exist only for the fixed builtin program ids is broken and the outcome is Loss of Funds (theft of funds without the owner's signature)?

## Target
- File/function: `program-runtime/src/program_cache_entry.rs` -> `load`
- Entrypoint: deploys and invokes its own programs whose cache entries carry verification and tombstone state, upgrading the program twice within one block
- Attacker controls: the ELF bytes, the loader used, deployment and upgrade slots, and invocation frequency
- Exploit idea: Get new_builtin used for an attacker-deployed program so it bypasses verification and metering.
- Invariant to test: Builtin entries exist only for the fixed builtin program ids.
- Expected Immunefi impact: Critical - Loss of Funds (theft of funds without the owner's signature)
- Fast validation: unit-test entry construction and assert tombstone, effective slot and owner fields match the on-chain account
