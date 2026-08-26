# Q1638: rent_collector::clone_with_epoch - epoch cloning loses accumulated state

## Question
Can an unprivileged attacker who creates and mutates accounts whose rent status the collector evaluates, sizing the account so its exemption minimum sits at an arithmetic boundary, drive `rent_collector::clone_with_epoch` to use clone_with_epoch so rent bookkeeping resets and capitalization no longer balances, so that the invariant that rent accounting is continuous across epoch transitions is broken and the outcome is Loss of Funds (lamport inflation / supply corruption)?

## Target
- File/function: `runtime/src/rent_collector.rs` -> `clone_with_epoch`
- Entrypoint: creates and mutates accounts whose rent status the collector evaluates, sizing the account so its exemption minimum sits at an arithmetic boundary
- Attacker controls: account data length, lamport balance, and the epoch at which the account is touched
- Exploit idea: Use clone_with_epoch so rent bookkeeping resets and capitalization no longer balances.
- Invariant to test: Rent accounting is continuous across epoch transitions.
- Expected Immunefi impact: Critical - Loss of Funds (lamport inflation / supply corruption)
- Fast validation: unit-test the rent collector against the crafted account and assert collected rent and epoch bookkeeping are correct
