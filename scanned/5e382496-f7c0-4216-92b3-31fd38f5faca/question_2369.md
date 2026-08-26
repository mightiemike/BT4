# Q2369: loaded_programs::percent_of_max_entries - cache growth from cheap deployments degrades block production

## Question
Can an unprivileged attacker who deploys, upgrades, closes and invokes its own programs to drive the program cache, upgrading the program in the slot immediately before invoking it, drive `loaded_programs::percent_of_max_entries` to deploy many small programs so cache pressure and eviction dominate replay time, so that the invariant that program cache work per block is bounded by the fees paid for deployments is broken and the outcome is Liveness/Loss of Availability (block production degraded below usable capacity)?

## Target
- File/function: `program-runtime/src/loaded_programs.rs` -> `percent_of_max_entries`
- Entrypoint: deploys, upgrades, closes and invokes its own programs to drive the program cache, upgrading the program in the slot immediately before invoking it
- Attacker controls: deployment and upgrade timing, program sizes, how often each program is invoked, and fork placement
- Exploit idea: Deploy many small programs so cache pressure and eviction dominate replay time.
- Invariant to test: Program cache work per block is bounded by the fees paid for deployments.
- Expected Immunefi impact: High - Liveness/Loss of Availability (block production degraded below usable capacity)
- Fast validation: program-runtime unit test driving the crafted deploy/invoke/prune sequence and asserting the extracted entry matches the on-chain account
