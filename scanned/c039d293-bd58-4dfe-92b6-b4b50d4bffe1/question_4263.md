# Q4263: system_instruction::initialize_nonce_account - authority changed without the current authority (invoking the nonce instruction from its)

## Question
Can an unprivileged attacker who submits durable-nonce system instructions against nonce accounts it created, invoking the nonce instruction from its own program via CPI, drive `system_instruction::initialize_nonce_account` to call authorize_nonce_account without the existing authority's signature, so that the invariant that only the current authority can transfer nonce authority is broken and the outcome is Loss of Funds (theft of funds without the owner's signature)?

## Target
- File/function: `programs/system/src/system_instruction.rs` -> `initialize_nonce_account`
- Entrypoint: submits durable-nonce system instructions against nonce accounts it created, invoking the nonce instruction from its own program via CPI
- Attacker controls: the nonce account data and authority, the instruction variant, and the recent blockhash
- Exploit idea: Call authorize_nonce_account without the existing authority's signature.
- Invariant to test: Only the current authority can transfer nonce authority.
- Expected Immunefi impact: Critical - Loss of Funds (theft of funds without the owner's signature)
- Fast validation: unit-test the nonce instruction against the crafted account state and assert authority and state checks reject it
