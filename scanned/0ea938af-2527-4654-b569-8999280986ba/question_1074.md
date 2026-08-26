# Q1074: fee::from - prioritization fee not included in the collected total (including many precompile signatures alongside a)

## Question
Can an unprivileged attacker who submits a transaction whose fee is computed from its signatures, compute budget and blockhash fee rate, including many precompile signatures alongside a single transaction signature, drive `fee::from` to have the priority component omitted from the charged total while still granting priority, so that the invariant that priority granted implies the priority fee was charged and recorded is broken and the outcome is Loss of Funds (fee, rent or reward undercharge draining protocol economics)?

## Target
- File/function: `fee/src/lib.rs` -> `from`
- Entrypoint: submits a transaction whose fee is computed from its signatures, compute budget and blockhash fee rate, including many precompile signatures alongside a single transaction signature
- Attacker controls: signature count, precompile signature count, compute unit limit and price, and the blockhash chosen
- Exploit idea: Have the priority component omitted from the charged total while still granting priority.
- Invariant to test: Priority granted implies the priority fee was charged and recorded.
- Expected Immunefi impact: High - Loss of Funds (fee, rent or reward undercharge draining protocol economics)
- Fast validation: unit-test calculate_fee/calculate_fee_details on the crafted transaction and assert the fee equals the manual expected value
