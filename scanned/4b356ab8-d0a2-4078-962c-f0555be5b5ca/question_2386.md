# Q2386: loaded_programs::remove_programs - closed program resurrected from cache (deploying hundreds of tiny programs in)

## Question
Can an unprivileged attacker who deploys, upgrades, closes and invokes its own programs to drive the program cache, deploying hundreds of tiny programs in a single block to force eviction, drive `loaded_programs::remove_programs` to invoke a program after closing it and have the cache return the old entry, so that the invariant that closing a program invalidates every cached entry for it is broken and the outcome is Loss of Funds (theft of funds without the owner's signature)?

## Target
- File/function: `program-runtime/src/loaded_programs.rs` -> `remove_programs`
- Entrypoint: deploys, upgrades, closes and invokes its own programs to drive the program cache, deploying hundreds of tiny programs in a single block to force eviction
- Attacker controls: deployment and upgrade timing, program sizes, how often each program is invoked, and fork placement
- Exploit idea: Invoke a program after closing it and have the cache return the old entry.
- Invariant to test: Closing a program invalidates every cached entry for it.
- Expected Immunefi impact: Critical - Loss of Funds (theft of funds without the owner's signature)
- Fast validation: program-runtime unit test driving the crafted deploy/invoke/prune sequence and asserting the extracted entry matches the on-chain account
