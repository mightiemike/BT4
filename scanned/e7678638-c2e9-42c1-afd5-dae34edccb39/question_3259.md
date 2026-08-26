# Q3259: instruction_context::try_borrow_instruction_account - writable flag read from the wrong entry (invoking the instruction from a CPI)

## Question
Can an unprivileged attacker who invokes its own program with a crafted instruction account list, invoking the instruction from a CPI callee two levels deep, drive `instruction_context::try_borrow_instruction_account` to make is_instruction_account_writable report writable for an account the message marked readonly, so that the invariant that instruction-level writability never exceeds message-level writability is broken and the outcome is Loss of Funds (theft of funds without the owner's signature)?

## Target
- File/function: `transaction-context/src/instruction.rs` -> `try_borrow_instruction_account`
- Entrypoint: invokes its own program with a crafted instruction account list, invoking the instruction from a CPI callee two levels deep
- Attacker controls: instruction account indexes, duplicate entries, signer and writable flags, instruction data and program index
- Exploit idea: Make is_instruction_account_writable report writable for an account the message marked readonly.
- Invariant to test: Instruction-level writability never exceeds message-level writability.
- Expected Immunefi impact: Critical - Loss of Funds (theft of funds without the owner's signature)
- Fast validation: unit-test the instruction context accessors against the crafted account list and assert privileges and indexes are exact
