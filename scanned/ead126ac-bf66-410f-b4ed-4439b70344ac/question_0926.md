# Q926: compute_budget_instruction_details::try_from - heap cost not charged for the granted heap

## Question
Can an unprivileged attacker who submits a transaction carrying ComputeBudget instructions it authored, placing the second, conflicting compute budget instruction after a CPI-invoking instruction, drive `compute_budget_instruction_details::try_from` to obtain a large heap frame while the compute units deducted correspond to the default heap, so that the invariant that heap cost in compute units is monotone in the heap size actually granted is broken and the outcome is Loss of Funds (fee, rent or reward undercharge draining protocol economics)?

## Target
- File/function: `compute-budget-instruction/src/compute_budget_instruction_details.rs` -> `try_from`
- Entrypoint: submits a transaction carrying ComputeBudget instructions it authored, placing the second, conflicting compute budget instruction after a CPI-invoking instruction
- Attacker controls: the number, order and payload of SetComputeUnitLimit, SetComputeUnitPrice, RequestHeapFrame and SetLoadedAccountsDataSizeLimit instructions
- Exploit idea: Obtain a large heap frame while the compute units deducted correspond to the default heap.
- Invariant to test: Heap cost in compute units is monotone in the heap size actually granted.
- Expected Immunefi impact: High - Loss of Funds (fee, rent or reward undercharge draining protocol economics)
- Fast validation: unit-test try_from/sanitize_and_convert_to_compute_budget_limits on the crafted instruction set and assert the derived limits match the charged fee
