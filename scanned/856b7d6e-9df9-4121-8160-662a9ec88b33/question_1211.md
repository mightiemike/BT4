# Q1211: cost_tracker::set_limits - limit inheritance from parent bank wrong (having each transaction declare the maximum)

## Question
Can an unprivileged attacker who submits many transactions into a leader's block, choosing their declared costs and write sets, having each transaction declare the maximum compute unit limit and then fail immediately, drive `cost_tracker::set_limits` to exploit new_from_parent_limits so a child bank starts with limits that do not match consensus expectations, so that the invariant that block limits at a slot are identical on every node is broken and the outcome is Consensus/Safety Violation (bank hash divergence or fork)?

## Target
- File/function: `cost-model/src/cost_tracker.rs` -> `set_limits`
- Entrypoint: submits many transactions into a leader's block, choosing their declared costs and write sets, having each transaction declare the maximum compute unit limit and then fail immediately
- Attacker controls: per-transaction declared cost, which accounts are write-locked, submission rate and ordering
- Exploit idea: Exploit new_from_parent_limits so a child bank starts with limits that do not match consensus expectations.
- Invariant to test: Block limits at a slot are identical on every node.
- Expected Immunefi impact: Critical - Consensus/Safety Violation (bank hash divergence or fork)
- Fast validation: unit-test the tracker with the crafted add/update/rollback sequence and assert block and per-account totals return to the correct value
