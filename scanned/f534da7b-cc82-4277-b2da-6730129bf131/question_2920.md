# Q2920: transaction_context::pop - instruction frame pushed without matching pop

## Question
Can an unprivileged attacker who invokes its own program which pushes and pops instruction frames and sets return data, returning an error from a nested CPI so unwinding runs the pop path, drive `transaction_context::pop` to leave a frame on the stack after an error so the next instruction inherits its privileges, so that the invariant that the instruction stack returns to its prior height after every instruction is broken and the outcome is Loss of Funds (theft of funds without the owner's signature)?

## Target
- File/function: `transaction-context/src/transaction.rs` -> `pop`
- Entrypoint: invokes its own program which pushes and pops instruction frames and sets return data, returning an error from a nested CPI so unwinding runs the pop path
- Attacker controls: the account list, instruction nesting, return data contents and which accounts are duplicated
- Exploit idea: Leave a frame on the stack after an error so the next instruction inherits its privileges.
- Invariant to test: The instruction stack returns to its prior height after every instruction.
- Expected Immunefi impact: Critical - Loss of Funds (theft of funds without the owner's signature)
- Fast validation: unit-test the push/pop and return-data sequence and assert frame state and account keys stay consistent
