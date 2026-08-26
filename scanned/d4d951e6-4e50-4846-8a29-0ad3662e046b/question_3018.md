# Q3018: transaction_context::from - deconstruction returns accounts not owned by the context (listing the same account at two)

## Question
Can an unprivileged attacker who invokes its own program which pushes and pops instruction frames and sets return data, listing the same account at two indexes with different privileges, drive `transaction_context::from` to make deconstruct_without_keys emit account state that was never part of this transaction, so that the invariant that deconstruction yields exactly the accounts the context was constructed with is broken and the outcome is Consensus/Safety Violation (bank hash divergence or fork)?

## Target
- File/function: `transaction-context/src/transaction.rs` -> `from`
- Entrypoint: invokes its own program which pushes and pops instruction frames and sets return data, listing the same account at two indexes with different privileges
- Attacker controls: the account list, instruction nesting, return data contents and which accounts are duplicated
- Exploit idea: Make deconstruct_without_keys emit account state that was never part of this transaction.
- Invariant to test: Deconstruction yields exactly the accounts the context was constructed with.
- Expected Immunefi impact: Critical - Consensus/Safety Violation (bank hash divergence or fork)
- Fast validation: unit-test the push/pop and return-data sequence and assert frame state and account keys stay consistent
