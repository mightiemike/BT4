# Q2932: transaction_context::pop - return data survives across an unrelated instruction

## Question
Can an unprivileged attacker who invokes its own program which pushes and pops instruction frames and sets return data, returning an error from a nested CPI so unwinding runs the pop path, drive `transaction_context::pop` to leave stale return data visible to an instruction that did not invoke the producer, so that the invariant that return data is cleared when a new top-level instruction begins is broken and the outcome is Loss of Funds (theft of funds without the owner's signature)?

## Target
- File/function: `transaction-context/src/transaction.rs` -> `pop`
- Entrypoint: invokes its own program which pushes and pops instruction frames and sets return data, returning an error from a nested CPI so unwinding runs the pop path
- Attacker controls: the account list, instruction nesting, return data contents and which accounts are duplicated
- Exploit idea: Leave stale return data visible to an instruction that did not invoke the producer.
- Invariant to test: Return data is cleared when a new top-level instruction begins.
- Expected Immunefi impact: Critical - Loss of Funds (theft of funds without the owner's signature)
- Fast validation: unit-test the push/pop and return-data sequence and assert frame state and account keys stay consistent
