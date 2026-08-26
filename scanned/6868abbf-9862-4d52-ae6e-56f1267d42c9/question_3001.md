# Q3001: transaction_context::pop - return data attributed to the wrong program (listing the same account at two)

## Question
Can an unprivileged attacker who invokes its own program which pushes and pops instruction frames and sets return data, listing the same account at two indexes with different privileges, drive `transaction_context::pop` to set return data that a later instruction reads as coming from a different program id, so that the invariant that return data always carries the program id that produced it is broken and the outcome is Loss of Funds (theft of funds without the owner's signature)?

## Target
- File/function: `transaction-context/src/transaction.rs` -> `pop`
- Entrypoint: invokes its own program which pushes and pops instruction frames and sets return data, listing the same account at two indexes with different privileges
- Attacker controls: the account list, instruction nesting, return data contents and which accounts are duplicated
- Exploit idea: Set return data that a later instruction reads as coming from a different program id.
- Invariant to test: Return data always carries the program id that produced it.
- Expected Immunefi impact: Critical - Loss of Funds (theft of funds without the owner's signature)
- Fast validation: unit-test the push/pop and return-data sequence and assert frame state and account keys stay consistent
