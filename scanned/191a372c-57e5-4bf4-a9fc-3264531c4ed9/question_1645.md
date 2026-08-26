# Q1645: rent_collector::new - deprecated exemption threshold path diverges (touching the account in the first)

## Question
Can an unprivileged attacker who creates and mutates accounts whose rent status the collector evaluates, touching the account in the first slot of a new epoch, drive `rent_collector::new` to trigger deprecate_rent_exemption_threshold so two nodes compute different exemption minima, so that the invariant that exemption threshold behaviour changes atomically at feature activation for all nodes is broken and the outcome is Consensus/Safety Violation (bank hash divergence or fork)?

## Target
- File/function: `runtime/src/rent_collector.rs` -> `new`
- Entrypoint: creates and mutates accounts whose rent status the collector evaluates, touching the account in the first slot of a new epoch
- Attacker controls: account data length, lamport balance, and the epoch at which the account is touched
- Exploit idea: Trigger deprecate_rent_exemption_threshold so two nodes compute different exemption minima.
- Invariant to test: Exemption threshold behaviour changes atomically at feature activation for all nodes.
- Expected Immunefi impact: Critical - Consensus/Safety Violation (bank hash divergence or fork)
- Fast validation: unit-test the rent collector against the crafted account and assert collected rent and epoch bookkeeping are correct
