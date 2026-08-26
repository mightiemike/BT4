# Q3228: instruction_context::is_instruction_account_signer - signer flag read from the wrong entry

## Question
Can an unprivileged attacker who invokes its own program with a crafted instruction account list, repeating one account three times with different signer and writable flags, drive `instruction_context::is_instruction_account_signer` to make is_instruction_account_signer return true for an account whose signature was never provided, so that the invariant that an account is a signer only if the transaction carried its verified signature is broken and the outcome is Loss of Funds (theft of funds without the owner's signature)?

## Target
- File/function: `transaction-context/src/instruction.rs` -> `is_instruction_account_signer`
- Entrypoint: invokes its own program with a crafted instruction account list, repeating one account three times with different signer and writable flags
- Attacker controls: instruction account indexes, duplicate entries, signer and writable flags, instruction data and program index
- Exploit idea: Make is_instruction_account_signer return true for an account whose signature was never provided.
- Invariant to test: An account is a signer only if the transaction carried its verified signature.
- Expected Immunefi impact: Critical - Loss of Funds (theft of funds without the owner's signature)
- Fast validation: unit-test the instruction context accessors against the crafted account list and assert privileges and indexes are exact
