# Q1946: cpi::translate_accounts_common - key pointer swapped so privileges bind to the wrong key (passing the caller's own program account)

## Question
Can an unprivileged attacker who invokes its own SBF program which issues sol_invoke_signed with attacker-built AccountInfo structures, passing the caller's own program account as one of the CPI accounts, drive `cpi::translate_accounts_common` to make key_addr resolve to a different pubkey than the account whose privileges are applied, so that the invariant that an account's key and its privileges always come from the same entry is broken and the outcome is Loss of Funds (theft of funds without the owner's signature)?

## Target
- File/function: `program-runtime/src/cpi.rs` -> `translate_accounts_common`
- Entrypoint: invokes its own SBF program which issues sol_invoke_signed with attacker-built AccountInfo structures, passing the caller's own program account as one of the CPI accounts
- Attacker controls: every pointer, length and capacity field in the AccountInfo and Instruction structs it passes to CPI
- Exploit idea: Make key_addr resolve to a different pubkey than the account whose privileges are applied.
- Invariant to test: An account's key and its privileges always come from the same entry.
- Expected Immunefi impact: Critical - Loss of Funds (theft of funds without the owner's signature)
- Fast validation: program-runtime unit test performing the crafted CPI and asserting translation rejects the pointers or preserves caller state
