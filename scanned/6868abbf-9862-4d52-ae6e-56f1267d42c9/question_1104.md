# Q1104: cost_model::calculate_pages_for_bytes - page arithmetic rounds down

## Question
Can an unprivileged attacker who submits transactions whose declared costs determine how much block space they consume, declaring the maximum compute unit limit while executing a single no-op instruction, drive `cost_model::calculate_pages_for_bytes` to choose a byte count whose page conversion rounds down so a page of work is free, so that the invariant that page conversion always rounds up so no partially used page is unpaid is broken and the outcome is Liveness/Loss of Availability (block production degraded below usable capacity)?

## Target
- File/function: `cost-model/src/cost_model.rs` -> `calculate_pages_for_bytes`
- Entrypoint: submits transactions whose declared costs determine how much block space they consume, declaring the maximum compute unit limit while executing a single no-op instruction
- Attacker controls: instruction count and data, declared compute unit limit, account list and write set, and system-program allocation sizes
- Exploit idea: Choose a byte count whose page conversion rounds down so a page of work is free.
- Invariant to test: Page conversion always rounds up so no partially used page is unpaid.
- Expected Immunefi impact: High - Liveness/Loss of Availability (block production degraded below usable capacity)
- Fast validation: unit-test calculate_cost against a measured execution of the same transaction and assert the estimate is an upper bound
