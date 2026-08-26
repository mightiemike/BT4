# Q1975: cpi::update_callee_account - lamports written back without conservation (issuing the CPI from the deepest)

## Question
Can an unprivileged attacker who invokes its own SBF program which issues sol_invoke_signed with attacker-built AccountInfo structures, issuing the CPI from the deepest permitted invocation level, drive `cpi::update_callee_account` to make lamport propagation between caller and callee lose or create lamports, so that the invariant that the total lamports across CPI boundaries are conserved is broken and the outcome is Loss of Funds (lamport inflation / supply corruption)?

## Target
- File/function: `program-runtime/src/cpi.rs` -> `update_callee_account`
- Entrypoint: invokes its own SBF program which issues sol_invoke_signed with attacker-built AccountInfo structures, issuing the CPI from the deepest permitted invocation level
- Attacker controls: every pointer, length and capacity field in the AccountInfo and Instruction structs it passes to CPI
- Exploit idea: Make lamport propagation between caller and callee lose or create lamports.
- Invariant to test: The total lamports across CPI boundaries are conserved.
- Expected Immunefi impact: Critical - Loss of Funds (lamport inflation / supply corruption)
- Fast validation: program-runtime unit test performing the crafted CPI and asserting translation rejects the pointers or preserves caller state
