# Q948: compute_budget_instruction_details::sanitize_requested_heap_size - heap cost not charged for the granted heap (requesting the maximum heap frame together)

## Question
Can an unprivileged attacker who submits a transaction carrying ComputeBudget instructions it authored, requesting the maximum heap frame together with the minimum compute unit limit, drive `compute_budget_instruction_details::sanitize_requested_heap_size` to obtain a large heap frame while the compute units deducted correspond to the default heap, so that the invariant that heap cost in compute units is monotone in the heap size actually granted is broken and the outcome is Loss of Funds (fee, rent or reward undercharge draining protocol economics)?

## Target
- File/function: `compute-budget-instruction/src/compute_budget_instruction_details.rs` -> `sanitize_requested_heap_size`
- Entrypoint: submits a transaction carrying ComputeBudget instructions it authored, requesting the maximum heap frame together with the minimum compute unit limit
- Attacker controls: the number, order and payload of SetComputeUnitLimit, SetComputeUnitPrice, RequestHeapFrame and SetLoadedAccountsDataSizeLimit instructions
- Exploit idea: Obtain a large heap frame while the compute units deducted correspond to the default heap.
- Invariant to test: Heap cost in compute units is monotone in the heap size actually granted.
- Expected Immunefi impact: High - Loss of Funds (fee, rent or reward undercharge draining protocol economics)
- Fast validation: unit-test try_from/sanitize_and_convert_to_compute_budget_limits on the crafted instruction set and assert the derived limits match the charged fee
