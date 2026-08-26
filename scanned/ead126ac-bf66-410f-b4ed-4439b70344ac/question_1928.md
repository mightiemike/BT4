# Q1928: cpi::check_authorized_program - authorized-program check bypassed (passing the caller's own program account)

## Question
Can an unprivileged attacker who invokes its own SBF program which issues sol_invoke_signed with attacker-built AccountInfo structures, passing the caller's own program account as one of the CPI accounts, drive `cpi::check_authorized_program` to invoke a program that check_authorized_program should forbid from being called via CPI, so that the invariant that programs excluded from CPI can never be invoked from another program is broken and the outcome is Loss of Funds (theft of funds without the owner's signature)?

## Target
- File/function: `program-runtime/src/cpi.rs` -> `check_authorized_program`
- Entrypoint: invokes its own SBF program which issues sol_invoke_signed with attacker-built AccountInfo structures, passing the caller's own program account as one of the CPI accounts
- Attacker controls: every pointer, length and capacity field in the AccountInfo and Instruction structs it passes to CPI
- Exploit idea: Invoke a program that check_authorized_program should forbid from being called via CPI.
- Invariant to test: Programs excluded from CPI can never be invoked from another program.
- Expected Immunefi impact: Critical - Loss of Funds (theft of funds without the owner's signature)
- Fast validation: program-runtime unit test performing the crafted CPI and asserting translation rejects the pointers or preserves caller state
