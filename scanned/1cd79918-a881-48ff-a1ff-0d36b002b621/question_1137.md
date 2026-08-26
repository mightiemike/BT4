# Q1137: cost_model::calculate_loaded_accounts_data_size_cost - page arithmetic rounds down (performing all account allocation from inside)

## Question
Can an unprivileged attacker who submits transactions whose declared costs determine how much block space they consume, performing all account allocation from inside a deployed program via CPI, drive `cost_model::calculate_loaded_accounts_data_size_cost` to choose a byte count whose page conversion rounds down so a page of work is free, so that the invariant that page conversion always rounds up so no partially used page is unpaid is broken and the outcome is Liveness/Loss of Availability (block production degraded below usable capacity)?

## Target
- File/function: `cost-model/src/cost_model.rs` -> `calculate_loaded_accounts_data_size_cost`
- Entrypoint: submits transactions whose declared costs determine how much block space they consume, performing all account allocation from inside a deployed program via CPI
- Attacker controls: instruction count and data, declared compute unit limit, account list and write set, and system-program allocation sizes
- Exploit idea: Choose a byte count whose page conversion rounds down so a page of work is free.
- Invariant to test: Page conversion always rounds up so no partially used page is unpaid.
- Expected Immunefi impact: High - Liveness/Loss of Availability (block production degraded below usable capacity)
- Fast validation: unit-test calculate_cost against a measured execution of the same transaction and assert the estimate is an upper bound
