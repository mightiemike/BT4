# Q928: compute_budget_instruction_details::try_from - default CU limit computed from a miscounted instruction set

## Question
Can an unprivileged attacker who submits a transaction carrying ComputeBudget instructions it authored, placing the second, conflicting compute budget instruction after a CPI-invoking instruction, drive `compute_budget_instruction_details::try_from` to make calculate_default_compute_unit_limit derive a limit from a different instruction count than the one executed, so that the invariant that the default compute unit limit is derived from exactly the instructions that execute is broken and the outcome is Loss of Funds (fee, rent or reward undercharge draining protocol economics)?

## Target
- File/function: `compute-budget-instruction/src/compute_budget_instruction_details.rs` -> `try_from`
- Entrypoint: submits a transaction carrying ComputeBudget instructions it authored, placing the second, conflicting compute budget instruction after a CPI-invoking instruction
- Attacker controls: the number, order and payload of SetComputeUnitLimit, SetComputeUnitPrice, RequestHeapFrame and SetLoadedAccountsDataSizeLimit instructions
- Exploit idea: Make calculate_default_compute_unit_limit derive a limit from a different instruction count than the one executed.
- Invariant to test: The default compute unit limit is derived from exactly the instructions that execute.
- Expected Immunefi impact: High - Loss of Funds (fee, rent or reward undercharge draining protocol economics)
- Fast validation: unit-test try_from/sanitize_and_convert_to_compute_budget_limits on the crafted instruction set and assert the derived limits match the charged fee
