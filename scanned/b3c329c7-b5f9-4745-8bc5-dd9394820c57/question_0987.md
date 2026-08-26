# Q987: builtin_programs_filter::check_program_kind - builtin classified as non-builtin to skip its default cost

## Question
Can an unprivileged attacker who submits a transaction mixing builtin-program and BPF-program instructions, deploying its own program at an address chosen to collide with the filter's fast path, drive `builtin_programs_filter::check_program_kind` to get a builtin instruction classified as unknown so its migration-aware default cost is not charged, so that the invariant that every builtin instruction is charged its declared default cost is broken and the outcome is Loss of Funds (fee, rent or reward undercharge draining protocol economics)?

## Target
- File/function: `compute-budget-instruction/src/builtin_programs_filter.rs` -> `check_program_kind`
- Entrypoint: submits a transaction mixing builtin-program and BPF-program instructions, deploying its own program at an address chosen to collide with the filter's fast path
- Attacker controls: which program ids appear in the instruction list and in what order
- Exploit idea: Get a builtin instruction classified as unknown so its migration-aware default cost is not charged.
- Invariant to test: Every builtin instruction is charged its declared default cost.
- Expected Immunefi impact: High - Loss of Funds (fee, rent or reward undercharge draining protocol economics)
- Fast validation: unit-test get_program_kind/check_program_kind on the crafted program id set and assert the classification matches actual dispatch
