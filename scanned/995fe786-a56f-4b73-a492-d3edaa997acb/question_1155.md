# Q1155: cost_tracker::add_transaction_execution_cost - execution cost update exceeds the reserved estimate

## Question
Can an unprivileged attacker who submits many transactions into a leader's block, choosing their declared costs and write sets, submitting a burst of transactions that all write-lock one hot account, drive `cost_tracker::add_transaction_execution_cost` to make update_execution_cost raise a transaction's cost after the block limit check already passed, so that the invariant that a transaction's final cost never exceeds the cost reserved for it at admission is broken and the outcome is Liveness/Loss of Availability (block production degraded below usable capacity)?

## Target
- File/function: `cost-model/src/cost_tracker.rs` -> `add_transaction_execution_cost`
- Entrypoint: submits many transactions into a leader's block, choosing their declared costs and write sets, submitting a burst of transactions that all write-lock one hot account
- Attacker controls: per-transaction declared cost, which accounts are write-locked, submission rate and ordering
- Exploit idea: Make update_execution_cost raise a transaction's cost after the block limit check already passed.
- Invariant to test: A transaction's final cost never exceeds the cost reserved for it at admission.
- Expected Immunefi impact: High - Liveness/Loss of Availability (block production degraded below usable capacity)
- Fast validation: unit-test the tracker with the crafted add/update/rollback sequence and assert block and per-account totals return to the correct value
