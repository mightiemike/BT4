# Q1087: cost_model::calculate_transaction_cost - write lock cost ignores resolved lookup addresses

## Question
Can an unprivileged attacker who submits transactions whose declared costs determine how much block space they consume, declaring the maximum compute unit limit while executing a single no-op instruction, drive `cost_model::calculate_transaction_cost` to obtain write locks on more accounts than get_write_lock_cost charges for, so that the invariant that write lock cost counts every account the transaction write-locks after resolution is broken and the outcome is Liveness/Loss of Availability (block production degraded below usable capacity)?

## Target
- File/function: `cost-model/src/cost_model.rs` -> `calculate_transaction_cost`
- Entrypoint: submits transactions whose declared costs determine how much block space they consume, declaring the maximum compute unit limit while executing a single no-op instruction
- Attacker controls: instruction count and data, declared compute unit limit, account list and write set, and system-program allocation sizes
- Exploit idea: Obtain write locks on more accounts than get_write_lock_cost charges for.
- Invariant to test: Write lock cost counts every account the transaction write-locks after resolution.
- Expected Immunefi impact: High - Liveness/Loss of Availability (block production degraded below usable capacity)
- Fast validation: unit-test calculate_cost against a measured execution of the same transaction and assert the estimate is an upper bound
