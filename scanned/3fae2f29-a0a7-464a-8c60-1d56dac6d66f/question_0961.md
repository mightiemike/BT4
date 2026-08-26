# Q961: compute_budget_instruction_details::try_from - compute budget instruction hidden behind a non-canonical program id (requesting the maximum heap frame together)

## Question
Can an unprivileged attacker who submits a transaction carrying ComputeBudget instructions it authored, requesting the maximum heap frame together with the minimum compute unit limit, drive `compute_budget_instruction_details::try_from` to get an instruction treated as a compute budget directive despite not targeting the compute budget program, so that the invariant that only instructions addressed to the canonical compute budget program id change limits is broken and the outcome is Loss of Funds (fee, rent or reward undercharge draining protocol economics)?

## Target
- File/function: `compute-budget-instruction/src/compute_budget_instruction_details.rs` -> `try_from`
- Entrypoint: submits a transaction carrying ComputeBudget instructions it authored, requesting the maximum heap frame together with the minimum compute unit limit
- Attacker controls: the number, order and payload of SetComputeUnitLimit, SetComputeUnitPrice, RequestHeapFrame and SetLoadedAccountsDataSizeLimit instructions
- Exploit idea: Get an instruction treated as a compute budget directive despite not targeting the compute budget program.
- Invariant to test: Only instructions addressed to the canonical compute budget program id change limits.
- Expected Immunefi impact: High - Loss of Funds (fee, rent or reward undercharge draining protocol economics)
- Fast validation: unit-test try_from/sanitize_and_convert_to_compute_budget_limits on the crafted instruction set and assert the derived limits match the charged fee
