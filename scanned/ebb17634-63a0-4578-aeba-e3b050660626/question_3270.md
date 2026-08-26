# Q3270: instruction_context::get_signers - signer set includes a non-signing account (invoking the instruction from a CPI)

## Question
Can an unprivileged attacker who invokes its own program with a crafted instruction account list, invoking the instruction from a CPI callee two levels deep, drive `instruction_context::get_signers` to make get_signers include a key that did not sign, so a downstream builtin accepts it as authority, so that the invariant that the signer set equals the set of keys with verified signatures is broken and the outcome is Loss of Funds (theft of funds without the owner's signature)?

## Target
- File/function: `transaction-context/src/instruction.rs` -> `get_signers`
- Entrypoint: invokes its own program with a crafted instruction account list, invoking the instruction from a CPI callee two levels deep
- Attacker controls: instruction account indexes, duplicate entries, signer and writable flags, instruction data and program index
- Exploit idea: Make get_signers include a key that did not sign, so a downstream builtin accepts it as authority.
- Invariant to test: The signer set equals the set of keys with verified signatures.
- Expected Immunefi impact: Critical - Loss of Funds (theft of funds without the owner's signature)
- Fast validation: unit-test the instruction context accessors against the crafted account list and assert privileges and indexes are exact
