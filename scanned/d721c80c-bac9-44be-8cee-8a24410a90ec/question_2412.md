# Q2412: loaded_programs::drain_modified_entries - modified entries leak across transactions (deploying hundreds of tiny programs in)

## Question
Can an unprivileged attacker who deploys, upgrades, closes and invokes its own programs to drive the program cache, deploying hundreds of tiny programs in a single block to force eviction, drive `loaded_programs::drain_modified_entries` to make drain_modified_entries carry an entry from a failed transaction into subsequent execution, so that the invariant that entries created by a failed transaction never become visible is broken and the outcome is Consensus/Safety Violation (bank hash divergence or fork)?

## Target
- File/function: `program-runtime/src/loaded_programs.rs` -> `drain_modified_entries`
- Entrypoint: deploys, upgrades, closes and invokes its own programs to drive the program cache, deploying hundreds of tiny programs in a single block to force eviction
- Attacker controls: deployment and upgrade timing, program sizes, how often each program is invoked, and fork placement
- Exploit idea: Make drain_modified_entries carry an entry from a failed transaction into subsequent execution.
- Invariant to test: Entries created by a failed transaction never become visible.
- Expected Immunefi impact: Critical - Consensus/Safety Violation (bank hash divergence or fork)
- Fast validation: program-runtime unit test driving the crafted deploy/invoke/prune sequence and asserting the extracted entry matches the on-chain account
