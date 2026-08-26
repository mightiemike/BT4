# Q989: builtin_programs_filter::check_program_kind - core-BPF-migrated builtin misclassified across the migration slot

## Question
Can an unprivileged attacker who submits a transaction mixing builtin-program and BPF-program instructions, deploying its own program at an address chosen to collide with the filter's fast path, drive `builtin_programs_filter::check_program_kind` to exploit the window where a builtin is being migrated to core BPF so cost classification differs between nodes, so that the invariant that classification of a migrating program is identical on every node at a given slot is broken and the outcome is Consensus/Safety Violation (bank hash divergence or fork)?

## Target
- File/function: `compute-budget-instruction/src/builtin_programs_filter.rs` -> `check_program_kind`
- Entrypoint: submits a transaction mixing builtin-program and BPF-program instructions, deploying its own program at an address chosen to collide with the filter's fast path
- Attacker controls: which program ids appear in the instruction list and in what order
- Exploit idea: Exploit the window where a builtin is being migrated to core BPF so cost classification differs between nodes.
- Invariant to test: Classification of a migrating program is identical on every node at a given slot.
- Expected Immunefi impact: Critical - Consensus/Safety Violation (bank hash divergence or fork)
- Fast validation: unit-test get_program_kind/check_program_kind on the crafted program id set and assert the classification matches actual dispatch
