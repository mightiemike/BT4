# Q1535: nonce_info::try_advance_nonce - advance leaves the stored hash unchanged (making the nonce authority a PDA)

## Question
Can an unprivileged attacker who submits durable-nonce transactions using nonce accounts it created, making the nonce authority a PDA of its own deployed program, drive `nonce_info::try_advance_nonce` to have try_advance_nonce succeed while writing back the same durable nonce value, so that the invariant that a successful nonce advance always stores a different durable nonce is broken and the outcome is Loss of Funds (double-spend / replayed transfer)?

## Target
- File/function: `svm/src/nonce_info.rs` -> `try_advance_nonce`
- Entrypoint: submits durable-nonce transactions using nonce accounts it created, making the nonce authority a PDA of its own deployed program
- Attacker controls: the nonce account data, its authority, the stored blockhash, and when the transaction is resubmitted
- Exploit idea: Have try_advance_nonce succeed while writing back the same durable nonce value.
- Invariant to test: A successful nonce advance always stores a different durable nonce.
- Expected Immunefi impact: Critical - Loss of Funds (double-spend / replayed transfer)
- Fast validation: unit-test try_advance_nonce with the crafted nonce state and assert the stored hash strictly changes
