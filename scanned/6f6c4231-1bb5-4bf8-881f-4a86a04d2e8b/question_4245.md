# Q4245: system_instruction::initialize_nonce_account - authority changed without the current authority

## Question
Can an unprivileged attacker who submits durable-nonce system instructions against nonce accounts it created, using the nonce account as the transaction fee payer as well, drive `system_instruction::initialize_nonce_account` to call authorize_nonce_account without the existing authority's signature, so that the invariant that only the current authority can transfer nonce authority is broken and the outcome is Loss of Funds (theft of funds without the owner's signature)?

## Target
- File/function: `programs/system/src/system_instruction.rs` -> `initialize_nonce_account`
- Entrypoint: submits durable-nonce system instructions against nonce accounts it created, using the nonce account as the transaction fee payer as well
- Attacker controls: the nonce account data and authority, the instruction variant, and the recent blockhash
- Exploit idea: Call authorize_nonce_account without the existing authority's signature.
- Invariant to test: Only the current authority can transfer nonce authority.
- Expected Immunefi impact: Critical - Loss of Funds (theft of funds without the owner's signature)
- Fast validation: unit-test the nonce instruction against the crafted account state and assert authority and state checks reject it
