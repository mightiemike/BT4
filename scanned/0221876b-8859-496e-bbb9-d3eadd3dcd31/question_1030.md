# Q1030: compute_budget_limits::default - budget granted exceeds limits derived for fees (requesting the maximum heap frame in)

## Question
Can an unprivileged attacker who submits a transaction whose declared compute budget determines its fee and its VM configuration, requesting the maximum heap frame in the same transaction, drive `compute_budget_limits::default` to receive a ComputeBudget whose max units exceed the limits the fee was based on, so that the invariant that the granted budget never exceeds the limits used to price the transaction is broken and the outcome is Loss of Funds (fee, rent or reward undercharge draining protocol economics)?

## Target
- File/function: `compute-budget/src/compute_budget_limits.rs` -> `default`
- Entrypoint: submits a transaction whose declared compute budget determines its fee and its VM configuration, requesting the maximum heap frame in the same transaction
- Attacker controls: the requested compute unit limit, unit price, heap size and loaded-accounts data size
- Exploit idea: Receive a ComputeBudget whose max units exceed the limits the fee was based on.
- Invariant to test: The granted budget never exceeds the limits used to price the transaction.
- Expected Immunefi impact: High - Loss of Funds (fee, rent or reward undercharge draining protocol economics)
- Fast validation: unit-test get_compute_budget_and_limits / get_prioritization_fee with the crafted values and assert fee and budget agree
