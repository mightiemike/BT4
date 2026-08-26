# Q994: builtin_programs_filter::get_program_kind - index out of range on a crafted program id index

## Question
Can an unprivileged attacker who submits a transaction mixing builtin-program and BPF-program instructions, deploying its own program at an address chosen to collide with the filter's fast path, drive `builtin_programs_filter::get_program_kind` to supply a program_id_index the filter dereferences without bounds checking, so that the invariant that program id lookup is bounds-checked against the resolved account keys is broken and the outcome is Liveness/Loss of Availability (cluster halt requiring human intervention)?

## Target
- File/function: `compute-budget-instruction/src/builtin_programs_filter.rs` -> `get_program_kind`
- Entrypoint: submits a transaction mixing builtin-program and BPF-program instructions, deploying its own program at an address chosen to collide with the filter's fast path
- Attacker controls: which program ids appear in the instruction list and in what order
- Exploit idea: Supply a program_id_index the filter dereferences without bounds checking.
- Invariant to test: Program id lookup is bounds-checked against the resolved account keys.
- Expected Immunefi impact: Critical - Liveness/Loss of Availability (cluster halt requiring human intervention)
- Fast validation: unit-test get_program_kind/check_program_kind on the crafted program id set and assert the classification matches actual dispatch
