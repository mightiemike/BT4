# Q1546: nonce_info::account - fee rate carried in nonce state is attacker-chosen (making the nonce authority a PDA)

## Question
Can an unprivileged attacker who submits durable-nonce transactions using nonce accounts it created, making the nonce authority a PDA of its own deployed program, drive `nonce_info::account` to advance a nonce whose recorded lamports_per_signature is lower than the real rate so future transactions underpay, so that the invariant that the fee rate stored with a nonce is the network rate at advance time is broken and the outcome is Loss of Funds (fee, rent or reward undercharge draining protocol economics)?

## Target
- File/function: `svm/src/nonce_info.rs` -> `account`
- Entrypoint: submits durable-nonce transactions using nonce accounts it created, making the nonce authority a PDA of its own deployed program
- Attacker controls: the nonce account data, its authority, the stored blockhash, and when the transaction is resubmitted
- Exploit idea: Advance a nonce whose recorded lamports_per_signature is lower than the real rate so future transactions underpay.
- Invariant to test: The fee rate stored with a nonce is the network rate at advance time.
- Expected Immunefi impact: High - Loss of Funds (fee, rent or reward undercharge draining protocol economics)
- Fast validation: unit-test try_advance_nonce with the crafted nonce state and assert the stored hash strictly changes
