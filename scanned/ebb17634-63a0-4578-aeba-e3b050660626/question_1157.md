# Q1157: cost_tracker::get_cost_by_writable_accounts - per-account cost limit evaded

## Question
Can an unprivileged attacker who submits many transactions into a leader's block, choosing their declared costs and write sets, submitting a burst of transactions that all write-lock one hot account, drive `cost_tracker::get_cost_by_writable_accounts` to spread writes so get_cost_by_writable_accounts and get_account_limit never see the true per-account total, so that the invariant that the per-account cost limit bounds all writes to that account within a block is broken and the outcome is Liveness/Loss of Availability (block production degraded below usable capacity)?

## Target
- File/function: `cost-model/src/cost_tracker.rs` -> `get_cost_by_writable_accounts`
- Entrypoint: submits many transactions into a leader's block, choosing their declared costs and write sets, submitting a burst of transactions that all write-lock one hot account
- Attacker controls: per-transaction declared cost, which accounts are write-locked, submission rate and ordering
- Exploit idea: Spread writes so get_cost_by_writable_accounts and get_account_limit never see the true per-account total.
- Invariant to test: The per-account cost limit bounds all writes to that account within a block.
- Expected Immunefi impact: High - Liveness/Loss of Availability (block production degraded below usable capacity)
- Fast validation: unit-test the tracker with the crafted add/update/rollback sequence and assert block and per-account totals return to the correct value
