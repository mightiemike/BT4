# Q4246: system_instruction::advance_nonce_account - advance and withdraw in one transaction defeat replay protection

## Question
Can an unprivileged attacker who submits durable-nonce system instructions against nonce accounts it created, using the nonce account as the transaction fee payer as well, drive `system_instruction::advance_nonce_account` to combine instructions so the nonce is consumed but its stored hash is restored, so that the invariant that a consumed nonce always ends the transaction with a new stored hash is broken and the outcome is Loss of Funds (double-spend / replayed transfer)?

## Target
- File/function: `programs/system/src/system_instruction.rs` -> `advance_nonce_account`
- Entrypoint: submits durable-nonce system instructions against nonce accounts it created, using the nonce account as the transaction fee payer as well
- Attacker controls: the nonce account data and authority, the instruction variant, and the recent blockhash
- Exploit idea: Combine instructions so the nonce is consumed but its stored hash is restored.
- Invariant to test: A consumed nonce always ends the transaction with a new stored hash.
- Expected Immunefi impact: Critical - Loss of Funds (double-spend / replayed transfer)
- Fast validation: unit-test the nonce instruction against the crafted account state and assert authority and state checks reject it
