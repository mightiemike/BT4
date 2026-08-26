# Q2595: program_cache_entry::from - account owner recorded differs from the real loader (invoking the program from two forks)

## Question
Can an unprivileged attacker who deploys and invokes its own programs whose cache entries carry verification and tombstone state, invoking the program from two forks that diverged before the upgrade, drive `program_cache_entry::from` to make account_owner report a loader that does not own the program account, so that the invariant that the recorded owner matches the on-chain account owner is broken and the outcome is Loss of Funds (theft of funds without the owner's signature)?

## Target
- File/function: `program-runtime/src/program_cache_entry.rs` -> `from`
- Entrypoint: deploys and invokes its own programs whose cache entries carry verification and tombstone state, invoking the program from two forks that diverged before the upgrade
- Attacker controls: the ELF bytes, the loader used, deployment and upgrade slots, and invocation frequency
- Exploit idea: Make account_owner report a loader that does not own the program account.
- Invariant to test: The recorded owner matches the on-chain account owner.
- Expected Immunefi impact: Critical - Loss of Funds (theft of funds without the owner's signature)
- Fast validation: unit-test entry construction and assert tombstone, effective slot and owner fields match the on-chain account
