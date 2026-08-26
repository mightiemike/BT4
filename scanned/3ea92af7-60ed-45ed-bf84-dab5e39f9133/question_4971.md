# Q4971: bank::capitalization - capitalization drifts from the sum of account lamports (resizing a large account in the)

## Question
Can an unprivileged attacker who submits transactions that are loaded, executed, committed and hashed into the bank, resizing a large account in the same block that the bank freezes, drive `bank::capitalization` to commit a transaction after which capitalization no longer equals the total lamports in the accounts set, so that the invariant that capitalization always equals the sum of all account lamports is broken and the outcome is Loss of Funds (lamport inflation / supply corruption)?

## Target
- File/function: `runtime/src/bank.rs` -> `capitalization`
- Entrypoint: submits transactions that are loaded, executed, committed and hashed into the bank, resizing a large account in the same block that the bank freezes
- Attacker controls: transaction contents, account state it owns, submission timing across slot and epoch boundaries, and fork placement
- Exploit idea: Commit a transaction after which capitalization no longer equals the total lamports in the accounts set.
- Invariant to test: Capitalization always equals the sum of all account lamports.
- Expected Immunefi impact: Critical - Loss of Funds (lamport inflation / supply corruption)
- Fast validation: bank integration test replaying the crafted transaction sequence and asserting capitalization, bank hash and accounting are stable
