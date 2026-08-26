# Q3294: instruction_context::get_program_key - program key or owner resolved incorrectly (passing zero instruction accounts to a)

## Question
Can an unprivileged attacker who invokes its own program with a crafted instruction account list, passing zero instruction accounts to a builtin that expects several, drive `instruction_context::get_program_key` to make get_program_key or get_program_owner return values from a different account than the instruction's program, so that the invariant that program identity is resolved from the instruction's own program index is broken and the outcome is Loss of Funds (theft of funds without the owner's signature)?

## Target
- File/function: `transaction-context/src/instruction.rs` -> `get_program_key`
- Entrypoint: invokes its own program with a crafted instruction account list, passing zero instruction accounts to a builtin that expects several
- Attacker controls: instruction account indexes, duplicate entries, signer and writable flags, instruction data and program index
- Exploit idea: Make get_program_key or get_program_owner return values from a different account than the instruction's program.
- Invariant to test: Program identity is resolved from the instruction's own program index.
- Expected Immunefi impact: Critical - Loss of Funds (theft of funds without the owner's signature)
- Fast validation: unit-test the instruction context accessors against the crafted account list and assert privileges and indexes are exact
