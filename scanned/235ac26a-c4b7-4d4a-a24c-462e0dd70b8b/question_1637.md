# Q1637: rent_collector::default - rent collected from an exempt account

## Question
Can an unprivileged attacker who creates and mutates accounts whose rent status the collector evaluates, sizing the account so its exemption minimum sits at an arithmetic boundary, drive `rent_collector::default` to make an exempt account lose lamports to rent collection, so that the invariant that no rent is ever collected from a rent-exempt account is broken and the outcome is Loss of Funds (lamport inflation / supply corruption)?

## Target
- File/function: `runtime/src/rent_collector.rs` -> `default`
- Entrypoint: creates and mutates accounts whose rent status the collector evaluates, sizing the account so its exemption minimum sits at an arithmetic boundary
- Attacker controls: account data length, lamport balance, and the epoch at which the account is touched
- Exploit idea: Make an exempt account lose lamports to rent collection.
- Invariant to test: No rent is ever collected from a rent-exempt account.
- Expected Immunefi impact: Critical - Loss of Funds (lamport inflation / supply corruption)
- Fast validation: unit-test the rent collector against the crafted account and assert collected rent and epoch bookkeeping are correct
