# Q3249: instruction_context::get_instruction_data - instruction data slice not the executed data

## Question
Can an unprivileged attacker who invokes its own program with a crafted instruction account list, repeating one account three times with different signer and writable flags, drive `instruction_context::get_instruction_data` to make get_instruction_data return bytes different from those the transaction signed, so that the invariant that instruction data returned is the signed instruction data is broken and the outcome is Loss of Funds (theft of funds without the owner's signature)?

## Target
- File/function: `transaction-context/src/instruction.rs` -> `get_instruction_data`
- Entrypoint: invokes its own program with a crafted instruction account list, repeating one account three times with different signer and writable flags
- Attacker controls: instruction account indexes, duplicate entries, signer and writable flags, instruction data and program index
- Exploit idea: Make get_instruction_data return bytes different from those the transaction signed.
- Invariant to test: Instruction data returned is the signed instruction data.
- Expected Immunefi impact: Critical - Loss of Funds (theft of funds without the owner's signature)
- Fast validation: unit-test the instruction context accessors against the crafted account list and assert privileges and indexes are exact
