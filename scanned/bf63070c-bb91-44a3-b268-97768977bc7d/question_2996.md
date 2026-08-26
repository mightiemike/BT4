# Q2996: transaction_context::configure_instruction_at_index - next instruction context configured with widened privileges (listing the same account at two)

## Question
Can an unprivileged attacker who invokes its own program which pushes and pops instruction frames and sets return data, listing the same account at two indexes with different privileges, drive `transaction_context::configure_instruction_at_index` to configure the next CPI context with signer or writable flags the current frame does not hold, so that the invariant that a child frame's privileges are a subset of its parent's is broken and the outcome is Loss of Funds (theft of funds without the owner's signature)?

## Target
- File/function: `transaction-context/src/transaction.rs` -> `configure_instruction_at_index`
- Entrypoint: invokes its own program which pushes and pops instruction frames and sets return data, listing the same account at two indexes with different privileges
- Attacker controls: the account list, instruction nesting, return data contents and which accounts are duplicated
- Exploit idea: Configure the next CPI context with signer or writable flags the current frame does not hold.
- Invariant to test: A child frame's privileges are a subset of its parent's.
- Expected Immunefi impact: Critical - Loss of Funds (theft of funds without the owner's signature)
- Fast validation: unit-test the push/pop and return-data sequence and assert frame state and account keys stay consistent
