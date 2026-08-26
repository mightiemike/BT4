# Q1543: nonce_info::new - advance succeeds on an uninitialized nonce account (making the nonce authority a PDA)

## Question
Can an unprivileged attacker who submits durable-nonce transactions using nonce accounts it created, making the nonce authority a PDA of its own deployed program, drive `nonce_info::new` to advance a nonce account whose state is uninitialized so arbitrary data becomes nonce state, so that the invariant that only an initialized nonce account in the Initialized state can be advanced is broken and the outcome is Loss of Funds (theft of funds without the owner's signature)?

## Target
- File/function: `svm/src/nonce_info.rs` -> `new`
- Entrypoint: submits durable-nonce transactions using nonce accounts it created, making the nonce authority a PDA of its own deployed program
- Attacker controls: the nonce account data, its authority, the stored blockhash, and when the transaction is resubmitted
- Exploit idea: Advance a nonce account whose state is uninitialized so arbitrary data becomes nonce state.
- Invariant to test: Only an initialized nonce account in the Initialized state can be advanced.
- Expected Immunefi impact: Critical - Loss of Funds (theft of funds without the owner's signature)
- Fast validation: unit-test try_advance_nonce with the crafted nonce state and assert the stored hash strictly changes
