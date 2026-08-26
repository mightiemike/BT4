# Q1970: cpi::translate_instruction - authorized-program check bypassed (issuing the CPI from the deepest)

## Question
Can an unprivileged attacker who invokes its own SBF program which issues sol_invoke_signed with attacker-built AccountInfo structures, issuing the CPI from the deepest permitted invocation level, drive `cpi::translate_instruction` to invoke a program that check_authorized_program should forbid from being called via CPI, so that the invariant that programs excluded from CPI can never be invoked from another program is broken and the outcome is Loss of Funds (theft of funds without the owner's signature)?

## Target
- File/function: `program-runtime/src/cpi.rs` -> `translate_instruction`
- Entrypoint: invokes its own SBF program which issues sol_invoke_signed with attacker-built AccountInfo structures, issuing the CPI from the deepest permitted invocation level
- Attacker controls: every pointer, length and capacity field in the AccountInfo and Instruction structs it passes to CPI
- Exploit idea: Invoke a program that check_authorized_program should forbid from being called via CPI.
- Invariant to test: Programs excluded from CPI can never be invoked from another program.
- Expected Immunefi impact: Critical - Loss of Funds (theft of funds without the owner's signature)
- Fast validation: program-runtime unit test performing the crafted CPI and asserting translation rejects the pointers or preserves caller state
