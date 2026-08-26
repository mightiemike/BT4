# Q2923: transaction_context::get_next_instruction_context - next instruction context configured with widened privileges

## Question
Can an unprivileged attacker who invokes its own program which pushes and pops instruction frames and sets return data, returning an error from a nested CPI so unwinding runs the pop path, drive `transaction_context::get_next_instruction_context` to configure the next CPI context with signer or writable flags the current frame does not hold, so that the invariant that a child frame's privileges are a subset of its parent's is broken and the outcome is Loss of Funds (theft of funds without the owner's signature)?

## Target
- File/function: `transaction-context/src/transaction.rs` -> `get_next_instruction_context`
- Entrypoint: invokes its own program which pushes and pops instruction frames and sets return data, returning an error from a nested CPI so unwinding runs the pop path
- Attacker controls: the account list, instruction nesting, return data contents and which accounts are duplicated
- Exploit idea: Configure the next CPI context with signer or writable flags the current frame does not hold.
- Invariant to test: A child frame's privileges are a subset of its parent's.
- Expected Immunefi impact: Critical - Loss of Funds (theft of funds without the owner's signature)
- Fast validation: unit-test the push/pop and return-data sequence and assert frame state and account keys stay consistent
