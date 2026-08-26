# Q1011: builtin_programs_filter::new - BPF program classified as a builtin (submitting in the slot where the)

## Question
Can an unprivileged attacker who submits a transaction mixing builtin-program and BPF-program instructions, submitting in the slot where the builtin's core-BPF migration feature activates, drive `builtin_programs_filter::new` to get an attacker-deployed program classified as a builtin so it receives builtin default costs instead of its real cost, so that the invariant that program classification matches the program the runtime actually dispatches to is broken and the outcome is Liveness/Loss of Availability (block production degraded below usable capacity)?

## Target
- File/function: `compute-budget-instruction/src/builtin_programs_filter.rs` -> `new`
- Entrypoint: submits a transaction mixing builtin-program and BPF-program instructions, submitting in the slot where the builtin's core-BPF migration feature activates
- Attacker controls: which program ids appear in the instruction list and in what order
- Exploit idea: Get an attacker-deployed program classified as a builtin so it receives builtin default costs instead of its real cost.
- Invariant to test: Program classification matches the program the runtime actually dispatches to.
- Expected Immunefi impact: High - Liveness/Loss of Availability (block production degraded below usable capacity)
- Fast validation: unit-test get_program_kind/check_program_kind on the crafted program id set and assert the classification matches actual dispatch
