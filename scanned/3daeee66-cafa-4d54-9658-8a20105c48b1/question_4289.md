# Q4289: system_instruction::authorize_nonce_account - nonce advanced without the authority's signature (making the nonce authority a PDA)

## Question
Can an unprivileged attacker who submits durable-nonce system instructions against nonce accounts it created, making the nonce authority a PDA of its own program, drive `system_instruction::authorize_nonce_account` to advance a nonce account whose authority did not sign the transaction, so that the invariant that only the stored nonce authority can advance a nonce is broken and the outcome is Loss of Funds (theft of funds without the owner's signature)?

## Target
- File/function: `programs/system/src/system_instruction.rs` -> `authorize_nonce_account`
- Entrypoint: submits durable-nonce system instructions against nonce accounts it created, making the nonce authority a PDA of its own program
- Attacker controls: the nonce account data and authority, the instruction variant, and the recent blockhash
- Exploit idea: Advance a nonce account whose authority did not sign the transaction.
- Invariant to test: Only the stored nonce authority can advance a nonce.
- Expected Immunefi impact: Critical - Loss of Funds (theft of funds without the owner's signature)
- Fast validation: unit-test the nonce instruction against the crafted account state and assert authority and state checks reject it
