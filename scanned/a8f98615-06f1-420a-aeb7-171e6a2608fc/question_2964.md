# Q2964: transaction_context::push - return data attributed to the wrong program (setting return data in a CPI)

## Question
Can an unprivileged attacker who invokes its own program which pushes and pops instruction frames and sets return data, setting return data in a CPI callee and reading it from a sibling instruction, drive `transaction_context::push` to set return data that a later instruction reads as coming from a different program id, so that the invariant that return data always carries the program id that produced it is broken and the outcome is Loss of Funds (theft of funds without the owner's signature)?

## Target
- File/function: `transaction-context/src/transaction.rs` -> `push`
- Entrypoint: invokes its own program which pushes and pops instruction frames and sets return data, setting return data in a CPI callee and reading it from a sibling instruction
- Attacker controls: the account list, instruction nesting, return data contents and which accounts are duplicated
- Exploit idea: Set return data that a later instruction reads as coming from a different program id.
- Invariant to test: Return data always carries the program id that produced it.
- Expected Immunefi impact: Critical - Loss of Funds (theft of funds without the owner's signature)
- Fast validation: unit-test the push/pop and return-data sequence and assert frame state and account keys stay consistent
