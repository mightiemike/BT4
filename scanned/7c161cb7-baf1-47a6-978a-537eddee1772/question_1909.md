# Q1909: cpi::from_sol_account_info - AccountInfo pointer aliases a different account (passing the caller's own program account)

## Question
Can an unprivileged attacker who invokes its own SBF program which issues sol_invoke_signed with attacker-built AccountInfo structures, passing the caller's own program account as one of the CPI accounts, drive `cpi::from_sol_account_info` to pass an AccountInfo whose lamports or data pointer refers to another account's region, so that the invariant that each AccountInfo's pointers must lie in that account's own serialized region is broken and the outcome is Loss of Funds (theft of funds without the owner's signature)?

## Target
- File/function: `program-runtime/src/cpi.rs` -> `from_sol_account_info`
- Entrypoint: invokes its own SBF program which issues sol_invoke_signed with attacker-built AccountInfo structures, passing the caller's own program account as one of the CPI accounts
- Attacker controls: every pointer, length and capacity field in the AccountInfo and Instruction structs it passes to CPI
- Exploit idea: Pass an AccountInfo whose lamports or data pointer refers to another account's region.
- Invariant to test: Each AccountInfo's pointers must lie in that account's own serialized region.
- Expected Immunefi impact: Critical - Loss of Funds (theft of funds without the owner's signature)
- Fast validation: program-runtime unit test performing the crafted CPI and asserting translation rejects the pointers or preserves caller state
