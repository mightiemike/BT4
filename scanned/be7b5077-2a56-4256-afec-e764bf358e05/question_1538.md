# Q1538: nonce_info::address - nonce account address not bound to the account written (making the nonce authority a PDA)

## Question
Can an unprivileged attacker who submits durable-nonce transactions using nonce accounts it created, making the nonce authority a PDA of its own deployed program, drive `nonce_info::address` to make address() and account() refer to different accounts so the advance lands on the wrong nonce, so that the invariant that the advanced account is exactly the account the transaction designated as its nonce is broken and the outcome is Loss of Funds (double-spend / replayed transfer)?

## Target
- File/function: `svm/src/nonce_info.rs` -> `address`
- Entrypoint: submits durable-nonce transactions using nonce accounts it created, making the nonce authority a PDA of its own deployed program
- Attacker controls: the nonce account data, its authority, the stored blockhash, and when the transaction is resubmitted
- Exploit idea: Make address() and account() refer to different accounts so the advance lands on the wrong nonce.
- Invariant to test: The advanced account is exactly the account the transaction designated as its nonce.
- Expected Immunefi impact: Critical - Loss of Funds (double-spend / replayed transfer)
- Fast validation: unit-test try_advance_nonce with the crafted nonce state and assert the stored hash strictly changes
