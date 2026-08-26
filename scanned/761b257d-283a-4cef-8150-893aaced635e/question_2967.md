# Q2967: transaction_context::get_return_data - return data survives across an unrelated instruction (setting return data in a CPI)

## Question
Can an unprivileged attacker who invokes its own program which pushes and pops instruction frames and sets return data, setting return data in a CPI callee and reading it from a sibling instruction, drive `transaction_context::get_return_data` to leave stale return data visible to an instruction that did not invoke the producer, so that the invariant that return data is cleared when a new top-level instruction begins is broken and the outcome is Loss of Funds (theft of funds without the owner's signature)?

## Target
- File/function: `transaction-context/src/transaction.rs` -> `get_return_data`
- Entrypoint: invokes its own program which pushes and pops instruction frames and sets return data, setting return data in a CPI callee and reading it from a sibling instruction
- Attacker controls: the account list, instruction nesting, return data contents and which accounts are duplicated
- Exploit idea: Leave stale return data visible to an instruction that did not invoke the producer.
- Invariant to test: Return data is cleared when a new top-level instruction begins.
- Expected Immunefi impact: Critical - Loss of Funds (theft of funds without the owner's signature)
- Fast validation: unit-test the push/pop and return-data sequence and assert frame state and account keys stay consistent
