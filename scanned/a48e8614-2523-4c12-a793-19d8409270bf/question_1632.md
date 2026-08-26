# Q1632: rent_collector::deprecate_rent_exemption_threshold - deprecated exemption threshold path diverges

## Question
Can an unprivileged attacker who creates and mutates accounts whose rent status the collector evaluates, sizing the account so its exemption minimum sits at an arithmetic boundary, drive `rent_collector::deprecate_rent_exemption_threshold` to trigger deprecate_rent_exemption_threshold so two nodes compute different exemption minima, so that the invariant that exemption threshold behaviour changes atomically at feature activation for all nodes is broken and the outcome is Consensus/Safety Violation (bank hash divergence or fork)?

## Target
- File/function: `runtime/src/rent_collector.rs` -> `deprecate_rent_exemption_threshold`
- Entrypoint: creates and mutates accounts whose rent status the collector evaluates, sizing the account so its exemption minimum sits at an arithmetic boundary
- Attacker controls: account data length, lamport balance, and the epoch at which the account is touched
- Exploit idea: Trigger deprecate_rent_exemption_threshold so two nodes compute different exemption minima.
- Invariant to test: Exemption threshold behaviour changes atomically at feature activation for all nodes.
- Expected Immunefi impact: Critical - Consensus/Safety Violation (bank hash divergence or fork)
- Fast validation: unit-test the rent collector against the crafted account and assert collected rent and epoch bookkeeping are correct
