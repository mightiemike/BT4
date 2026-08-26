# Q1170: cost_tracker::new_from_parent_limits - limit inheritance from parent bank wrong

## Question
Can an unprivileged attacker who submits many transactions into a leader's block, choosing their declared costs and write sets, submitting a burst of transactions that all write-lock one hot account, drive `cost_tracker::new_from_parent_limits` to exploit new_from_parent_limits so a child bank starts with limits that do not match consensus expectations, so that the invariant that block limits at a slot are identical on every node is broken and the outcome is Consensus/Safety Violation (bank hash divergence or fork)?

## Target
- File/function: `cost-model/src/cost_tracker.rs` -> `new_from_parent_limits`
- Entrypoint: submits many transactions into a leader's block, choosing their declared costs and write sets, submitting a burst of transactions that all write-lock one hot account
- Attacker controls: per-transaction declared cost, which accounts are write-locked, submission rate and ordering
- Exploit idea: Exploit new_from_parent_limits so a child bank starts with limits that do not match consensus expectations.
- Invariant to test: Block limits at a slot are identical on every node.
- Expected Immunefi impact: Critical - Consensus/Safety Violation (bank hash divergence or fork)
- Fast validation: unit-test the tracker with the crafted add/update/rollback sequence and assert block and per-account totals return to the correct value
