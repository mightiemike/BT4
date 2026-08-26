# Q1148: cost_tracker::roll_back_applied_costs - rollback subtracts more than was added

## Question
Can an unprivileged attacker who submits many transactions into a leader's block, choosing their declared costs and write sets, submitting a burst of transactions that all write-lock one hot account, drive `cost_tracker::roll_back_applied_costs` to drive roll_back_applied_costs or remove so the block cost total underflows or drops below the real committed cost, so that the invariant that block cost equals the sum of committed transaction costs at all times is broken and the outcome is Consensus/Safety Violation (bank hash divergence or fork)?

## Target
- File/function: `cost-model/src/cost_tracker.rs` -> `roll_back_applied_costs`
- Entrypoint: submits many transactions into a leader's block, choosing their declared costs and write sets, submitting a burst of transactions that all write-lock one hot account
- Attacker controls: per-transaction declared cost, which accounts are write-locked, submission rate and ordering
- Exploit idea: Drive roll_back_applied_costs or remove so the block cost total underflows or drops below the real committed cost.
- Invariant to test: Block cost equals the sum of committed transaction costs at all times.
- Expected Immunefi impact: Critical - Consensus/Safety Violation (bank hash divergence or fork)
- Fast validation: unit-test the tracker with the crafted add/update/rollback sequence and assert block and per-account totals return to the correct value
