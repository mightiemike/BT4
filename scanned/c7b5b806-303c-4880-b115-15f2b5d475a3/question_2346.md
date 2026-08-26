# Q2346: loaded_programs::get_upcoming_environment_for_epoch - upcoming-epoch environment applied early

## Question
Can an unprivileged attacker who deploys, upgrades, closes and invokes its own programs to drive the program cache, upgrading the program in the slot immediately before invoking it, drive `loaded_programs::get_upcoming_environment_for_epoch` to make get_upcoming_environment_for_epoch take effect before the epoch boundary on some nodes, so that the invariant that environment transitions happen at the same epoch boundary on every node is broken and the outcome is Consensus/Safety Violation (bank hash divergence or fork)?

## Target
- File/function: `program-runtime/src/loaded_programs.rs` -> `get_upcoming_environment_for_epoch`
- Entrypoint: deploys, upgrades, closes and invokes its own programs to drive the program cache, upgrading the program in the slot immediately before invoking it
- Attacker controls: deployment and upgrade timing, program sizes, how often each program is invoked, and fork placement
- Exploit idea: Make get_upcoming_environment_for_epoch take effect before the epoch boundary on some nodes.
- Invariant to test: Environment transitions happen at the same epoch boundary on every node.
- Expected Immunefi impact: Critical - Consensus/Safety Violation (bank hash divergence or fork)
- Fast validation: program-runtime unit test driving the crafted deploy/invoke/prune sequence and asserting the extracted entry matches the on-chain account
