# Q921: compute_budget_instruction_details::sanitize_requested_heap_size - requested heap size escapes its bounds

## Question
Can an unprivileged attacker who submits a transaction carrying ComputeBudget instructions it authored, placing the second, conflicting compute budget instruction after a CPI-invoking instruction, drive `compute_budget_instruction_details::sanitize_requested_heap_size` to pass a heap size through sanitize_requested_heap_size that is not a multiple of the page granularity or exceeds the maximum, so that the invariant that the granted heap is always a bounded, page-aligned value that was fully charged for is broken and the outcome is Liveness/Loss of Availability (block production degraded below usable capacity)?

## Target
- File/function: `compute-budget-instruction/src/compute_budget_instruction_details.rs` -> `sanitize_requested_heap_size`
- Entrypoint: submits a transaction carrying ComputeBudget instructions it authored, placing the second, conflicting compute budget instruction after a CPI-invoking instruction
- Attacker controls: the number, order and payload of SetComputeUnitLimit, SetComputeUnitPrice, RequestHeapFrame and SetLoadedAccountsDataSizeLimit instructions
- Exploit idea: Pass a heap size through sanitize_requested_heap_size that is not a multiple of the page granularity or exceeds the maximum.
- Invariant to test: The granted heap is always a bounded, page-aligned value that was fully charged for.
- Expected Immunefi impact: High - Liveness/Loss of Availability (block production degraded below usable capacity)
- Fast validation: unit-test try_from/sanitize_and_convert_to_compute_budget_limits on the crafted instruction set and assert the derived limits match the charged fee
