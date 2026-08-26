# Q1006: builtin_programs_filter::check_program_kind - filter cache keyed on index rather than program id (invoking the builtin through CPI from)

## Question
Can an unprivileged attacker who submits a transaction mixing builtin-program and BPF-program instructions, invoking the builtin through CPI from its own program rather than at the top level, drive `builtin_programs_filter::check_program_kind` to reorder instructions so a cached classification is applied to a different program id, so that the invariant that cached classifications are keyed on the program id they were computed for is broken and the outcome is Liveness/Loss of Availability (block production degraded below usable capacity)?

## Target
- File/function: `compute-budget-instruction/src/builtin_programs_filter.rs` -> `check_program_kind`
- Entrypoint: submits a transaction mixing builtin-program and BPF-program instructions, invoking the builtin through CPI from its own program rather than at the top level
- Attacker controls: which program ids appear in the instruction list and in what order
- Exploit idea: Reorder instructions so a cached classification is applied to a different program id.
- Invariant to test: Cached classifications are keyed on the program id they were computed for.
- Expected Immunefi impact: High - Liveness/Loss of Availability (block production degraded below usable capacity)
- Fast validation: unit-test get_program_kind/check_program_kind on the crafted program id set and assert the classification matches actual dispatch
