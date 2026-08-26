# Q1912: cpi::update_caller_account - callee writes propagate to an account the caller never passed (passing the caller's own program account)

## Question
Can an unprivileged attacker who invokes its own SBF program which issues sol_invoke_signed with attacker-built AccountInfo structures, passing the caller's own program account as one of the CPI accounts, drive `cpi::update_caller_account` to make update_callee_account or update_caller_account write into an account outside the instruction's account list, so that the invariant that CPI only propagates changes for accounts present in both caller and callee account lists is broken and the outcome is Loss of Funds (theft of funds without the owner's signature)?

## Target
- File/function: `program-runtime/src/cpi.rs` -> `update_caller_account`
- Entrypoint: invokes its own SBF program which issues sol_invoke_signed with attacker-built AccountInfo structures, passing the caller's own program account as one of the CPI accounts
- Attacker controls: every pointer, length and capacity field in the AccountInfo and Instruction structs it passes to CPI
- Exploit idea: Make update_callee_account or update_caller_account write into an account outside the instruction's account list.
- Invariant to test: CPI only propagates changes for accounts present in both caller and callee account lists.
- Expected Immunefi impact: Critical - Loss of Funds (theft of funds without the owner's signature)
- Fast validation: program-runtime unit test performing the crafted CPI and asserting translation rejects the pointers or preserves caller state
