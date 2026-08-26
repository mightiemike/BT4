# Q1528: nonce_info::account - advance succeeds on an uninitialized nonce account

## Question
Can an unprivileged attacker who submits durable-nonce transactions using nonce accounts it created, resubmitting the identical nonce transaction after it failed during execution, drive `nonce_info::account` to advance a nonce account whose state is uninitialized so arbitrary data becomes nonce state, so that the invariant that only an initialized nonce account in the Initialized state can be advanced is broken and the outcome is Loss of Funds (theft of funds without the owner's signature)?

## Target
- File/function: `svm/src/nonce_info.rs` -> `account`
- Entrypoint: submits durable-nonce transactions using nonce accounts it created, resubmitting the identical nonce transaction after it failed during execution
- Attacker controls: the nonce account data, its authority, the stored blockhash, and when the transaction is resubmitted
- Exploit idea: Advance a nonce account whose state is uninitialized so arbitrary data becomes nonce state.
- Invariant to test: Only an initialized nonce account in the Initialized state can be advanced.
- Expected Immunefi impact: Critical - Loss of Funds (theft of funds without the owner's signature)
- Fast validation: unit-test try_advance_nonce with the crafted nonce state and assert the stored hash strictly changes
