# Q2435: loaded_programs::unload_program_entry - closed program resurrected from cache (invoking the program on two competing)

## Question
Can an unprivileged attacker who deploys, upgrades, closes and invokes its own programs to drive the program cache, invoking the program on two competing forks in the same slot, drive `loaded_programs::unload_program_entry` to invoke a program after closing it and have the cache return the old entry, so that the invariant that closing a program invalidates every cached entry for it is broken and the outcome is Loss of Funds (theft of funds without the owner's signature)?

## Target
- File/function: `program-runtime/src/loaded_programs.rs` -> `unload_program_entry`
- Entrypoint: deploys, upgrades, closes and invokes its own programs to drive the program cache, invoking the program on two competing forks in the same slot
- Attacker controls: deployment and upgrade timing, program sizes, how often each program is invoked, and fork placement
- Exploit idea: Invoke a program after closing it and have the cache return the old entry.
- Invariant to test: Closing a program invalidates every cached entry for it.
- Expected Immunefi impact: Critical - Loss of Funds (theft of funds without the owner's signature)
- Fast validation: program-runtime unit test driving the crafted deploy/invoke/prune sequence and asserting the extracted entry matches the on-chain account
