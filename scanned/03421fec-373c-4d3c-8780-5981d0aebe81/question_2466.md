# Q2466: loaded_programs::remove_programs - cache growth from cheap deployments degrades block production (invoking the program on two competing)

## Question
Can an unprivileged attacker who deploys, upgrades, closes and invokes its own programs to drive the program cache, invoking the program on two competing forks in the same slot, drive `loaded_programs::remove_programs` to deploy many small programs so cache pressure and eviction dominate replay time, so that the invariant that program cache work per block is bounded by the fees paid for deployments is broken and the outcome is Liveness/Loss of Availability (block production degraded below usable capacity)?

## Target
- File/function: `program-runtime/src/loaded_programs.rs` -> `remove_programs`
- Entrypoint: deploys, upgrades, closes and invokes its own programs to drive the program cache, invoking the program on two competing forks in the same slot
- Attacker controls: deployment and upgrade timing, program sizes, how often each program is invoked, and fork placement
- Exploit idea: Deploy many small programs so cache pressure and eviction dominate replay time.
- Invariant to test: Program cache work per block is bounded by the fees paid for deployments.
- Expected Immunefi impact: High - Liveness/Loss of Availability (block production degraded below usable capacity)
- Fast validation: program-runtime unit test driving the crafted deploy/invoke/prune sequence and asserting the extracted entry matches the on-chain account
