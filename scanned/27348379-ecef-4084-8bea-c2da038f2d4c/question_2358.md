# Q2358: loaded_programs::replenish - cooperative loading race yields two different entries

## Question
Can an unprivileged attacker who deploys, upgrades, closes and invokes its own programs to drive the program cache, upgrading the program in the slot immediately before invoking it, drive `loaded_programs::replenish` to race finish_cooperative_loading_task so two loaders publish different entries for one program, so that the invariant that cooperative loading publishes exactly one entry per (program, slot) is broken and the outcome is Consensus/Safety Violation (bank hash divergence or fork)?

## Target
- File/function: `program-runtime/src/loaded_programs.rs` -> `replenish`
- Entrypoint: deploys, upgrades, closes and invokes its own programs to drive the program cache, upgrading the program in the slot immediately before invoking it
- Attacker controls: deployment and upgrade timing, program sizes, how often each program is invoked, and fork placement
- Exploit idea: Race finish_cooperative_loading_task so two loaders publish different entries for one program.
- Invariant to test: Cooperative loading publishes exactly one entry per (program, slot).
- Expected Immunefi impact: Critical - Consensus/Safety Violation (bank hash divergence or fork)
- Fast validation: program-runtime unit test driving the crafted deploy/invoke/prune sequence and asserting the extracted entry matches the on-chain account
