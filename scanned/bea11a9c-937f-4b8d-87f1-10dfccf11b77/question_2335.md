# Q2335: loaded_programs::replenish - delay visibility tombstone not applied

## Question
Can an unprivileged attacker who deploys, upgrades, closes and invokes its own programs to drive the program cache, upgrading the program in the slot immediately before invoking it, drive `loaded_programs::replenish` to invoke a just-upgraded program without the delay-visibility tombstone so new bytecode runs a slot early, so that the invariant that a program upgraded in slot N is only executable from slot N+1 is broken and the outcome is Consensus/Safety Violation (bank hash divergence or fork)?

## Target
- File/function: `program-runtime/src/loaded_programs.rs` -> `replenish`
- Entrypoint: deploys, upgrades, closes and invokes its own programs to drive the program cache, upgrading the program in the slot immediately before invoking it
- Attacker controls: deployment and upgrade timing, program sizes, how often each program is invoked, and fork placement
- Exploit idea: Invoke a just-upgraded program without the delay-visibility tombstone so new bytecode runs a slot early.
- Invariant to test: A program upgraded in slot N is only executable from slot N+1.
- Expected Immunefi impact: Critical - Consensus/Safety Violation (bank hash divergence or fork)
- Fast validation: program-runtime unit test driving the crafted deploy/invoke/prune sequence and asserting the extracted entry matches the on-chain account
