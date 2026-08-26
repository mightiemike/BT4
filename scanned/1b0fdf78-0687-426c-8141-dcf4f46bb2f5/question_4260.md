# Q4260: system_instruction::authorize_nonce_account - re-initialization of a live nonce account (invoking the nonce instruction from its)

## Question
Can an unprivileged attacker who submits durable-nonce system instructions against nonce accounts it created, invoking the nonce instruction from its own program via CPI, drive `system_instruction::authorize_nonce_account` to initialize a nonce account that is already initialized so its stored hash and authority reset, so that the invariant that an initialized nonce account cannot be re-initialized is broken and the outcome is Loss of Funds (double-spend / replayed transfer)?

## Target
- File/function: `programs/system/src/system_instruction.rs` -> `authorize_nonce_account`
- Entrypoint: submits durable-nonce system instructions against nonce accounts it created, invoking the nonce instruction from its own program via CPI
- Attacker controls: the nonce account data and authority, the instruction variant, and the recent blockhash
- Exploit idea: Initialize a nonce account that is already initialized so its stored hash and authority reset.
- Invariant to test: An initialized nonce account cannot be re-initialized.
- Expected Immunefi impact: Critical - Loss of Funds (double-spend / replayed transfer)
- Fast validation: unit-test the nonce instruction against the crafted account state and assert authority and state checks reject it
