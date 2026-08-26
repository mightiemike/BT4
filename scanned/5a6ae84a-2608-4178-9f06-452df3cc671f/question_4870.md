# Q4870: bank::store_account_and_update_capitalization - burn or purge removes lamports without capitalization update

## Question
Can an unprivileged attacker who submits transactions that are loaded, executed, committed and hashed into the bank, landing the transaction in the last slot of an epoch, drive `bank::store_account_and_update_capitalization` to trigger burn_and_purge_account so lamports vanish without a matching capitalization change, so that the invariant that any lamport destruction is reflected in capitalization is broken and the outcome is Loss of Funds (lamport inflation / supply corruption)?

## Target
- File/function: `runtime/src/bank.rs` -> `store_account_and_update_capitalization`
- Entrypoint: submits transactions that are loaded, executed, committed and hashed into the bank, landing the transaction in the last slot of an epoch
- Attacker controls: transaction contents, account state it owns, submission timing across slot and epoch boundaries, and fork placement
- Exploit idea: Trigger burn_and_purge_account so lamports vanish without a matching capitalization change.
- Invariant to test: Any lamport destruction is reflected in capitalization.
- Expected Immunefi impact: Critical - Loss of Funds (lamport inflation / supply corruption)
- Fast validation: bank integration test replaying the crafted transaction sequence and asserting capitalization, bank hash and accounting are stable
