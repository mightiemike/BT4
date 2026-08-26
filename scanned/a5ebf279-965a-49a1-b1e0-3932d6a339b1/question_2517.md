# Q2517: program_cache_entry::is_tombstone - closed tombstone replaced by a live entry

## Question
Can an unprivileged attacker who deploys and invokes its own programs whose cache entries carry verification and tombstone state, deploying bytecode that fails verification and then invoking the address repeatedly, drive `program_cache_entry::is_tombstone` to overwrite a closed tombstone with an executable entry for the same address, so that the invariant that a closed program's tombstone is never replaced by an executable entry is broken and the outcome is Loss of Funds (theft of funds without the owner's signature)?

## Target
- File/function: `program-runtime/src/program_cache_entry.rs` -> `is_tombstone`
- Entrypoint: deploys and invokes its own programs whose cache entries carry verification and tombstone state, deploying bytecode that fails verification and then invoking the address repeatedly
- Attacker controls: the ELF bytes, the loader used, deployment and upgrade slots, and invocation frequency
- Exploit idea: Overwrite a closed tombstone with an executable entry for the same address.
- Invariant to test: A closed program's tombstone is never replaced by an executable entry.
- Expected Immunefi impact: Critical - Loss of Funds (theft of funds without the owner's signature)
- Fast validation: unit-test entry construction and assert tombstone, effective slot and owner fields match the on-chain account
