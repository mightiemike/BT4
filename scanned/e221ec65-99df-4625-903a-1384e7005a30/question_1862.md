# Q1862: cpi::translate_accounts_common - duplicate account entries desynchronise writebacks

## Question
Can an unprivileged attacker who invokes its own SBF program which issues sol_invoke_signed with attacker-built AccountInfo structures, using the C ABI variant of sol_invoke_signed rather than the Rust one, drive `cpi::translate_accounts_common` to pass the same account twice in the CPI account list so one copy's writeback overwrites the other, so that the invariant that duplicated accounts share one underlying borrow and one writeback is broken and the outcome is Loss of Funds (theft of funds without the owner's signature)?

## Target
- File/function: `program-runtime/src/cpi.rs` -> `translate_accounts_common`
- Entrypoint: invokes its own SBF program which issues sol_invoke_signed with attacker-built AccountInfo structures, using the C ABI variant of sol_invoke_signed rather than the Rust one
- Attacker controls: every pointer, length and capacity field in the AccountInfo and Instruction structs it passes to CPI
- Exploit idea: Pass the same account twice in the CPI account list so one copy's writeback overwrites the other.
- Invariant to test: Duplicated accounts share one underlying borrow and one writeback.
- Expected Immunefi impact: Critical - Loss of Funds (theft of funds without the owner's signature)
- Fast validation: program-runtime unit test performing the crafted CPI and asserting translation rejects the pointers or preserves caller state
