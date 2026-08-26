# Q4847: bank::simulate_transaction_unchecked - account overrides leak into real execution

## Question
Can an unprivileged attacker who submits transactions that are loaded, executed, committed and hashed into the bank, landing the transaction in the last slot of an epoch, drive `bank::simulate_transaction_unchecked` to get an account override intended for simulation applied to a committed transaction, so that the invariant that overrides are confined to simulation is broken and the outcome is Loss of Funds (theft of funds without the owner's signature)?

## Target
- File/function: `runtime/src/bank.rs` -> `simulate_transaction_unchecked`
- Entrypoint: submits transactions that are loaded, executed, committed and hashed into the bank, landing the transaction in the last slot of an epoch
- Attacker controls: transaction contents, account state it owns, submission timing across slot and epoch boundaries, and fork placement
- Exploit idea: Get an account override intended for simulation applied to a committed transaction.
- Invariant to test: Overrides are confined to simulation.
- Expected Immunefi impact: Critical - Loss of Funds (theft of funds without the owner's signature)
- Fast validation: bank integration test replaying the crafted transaction sequence and asserting capitalization, bank hash and accounting are stable
