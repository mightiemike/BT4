# Q1215: cost_tracker::find_number_of_contended_accounts - contended-account accounting used to stall block production (having each transaction declare the maximum)

## Question
Can an unprivileged attacker who submits many transactions into a leader's block, choosing their declared costs and write sets, having each transaction declare the maximum compute unit limit and then fail immediately, drive `cost_tracker::find_number_of_contended_accounts` to create write contention so find_number_of_contended_accounts or find_costliest_account causes the leader to stop packing, so that the invariant that contention statistics never prevent otherwise valid transactions from being packed is broken and the outcome is Liveness/Loss of Availability (block production degraded below usable capacity)?

## Target
- File/function: `cost-model/src/cost_tracker.rs` -> `find_number_of_contended_accounts`
- Entrypoint: submits many transactions into a leader's block, choosing their declared costs and write sets, having each transaction declare the maximum compute unit limit and then fail immediately
- Attacker controls: per-transaction declared cost, which accounts are write-locked, submission rate and ordering
- Exploit idea: Create write contention so find_number_of_contended_accounts or find_costliest_account causes the leader to stop packing.
- Invariant to test: Contention statistics never prevent otherwise valid transactions from being packed.
- Expected Immunefi impact: High - Liveness/Loss of Availability (block production degraded below usable capacity)
- Fast validation: unit-test the tracker with the crafted add/update/rollback sequence and assert block and per-account totals return to the correct value
