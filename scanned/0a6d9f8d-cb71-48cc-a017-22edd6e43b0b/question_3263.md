# Q3263: instruction_context::get_index_of_instruction_account_in_transaction - duplicate detection misses an aliased account (invoking the instruction from a CPI)

## Question
Can an unprivileged attacker who invokes its own program with a crafted instruction account list, invoking the instruction from a CPI callee two levels deep, drive `instruction_context::get_index_of_instruction_account_in_transaction` to make is_instruction_account_duplicate miss a repeated account so two borrows of one account are handed out, so that the invariant that duplicate detection identifies every repeated account index is broken and the outcome is Loss of Funds (theft of funds without the owner's signature)?

## Target
- File/function: `transaction-context/src/instruction.rs` -> `get_index_of_instruction_account_in_transaction`
- Entrypoint: invokes its own program with a crafted instruction account list, invoking the instruction from a CPI callee two levels deep
- Attacker controls: instruction account indexes, duplicate entries, signer and writable flags, instruction data and program index
- Exploit idea: Make is_instruction_account_duplicate miss a repeated account so two borrows of one account are handed out.
- Invariant to test: Duplicate detection identifies every repeated account index.
- Expected Immunefi impact: Critical - Loss of Funds (theft of funds without the owner's signature)
- Fast validation: unit-test the instruction context accessors against the crafted account list and assert privileges and indexes are exact
