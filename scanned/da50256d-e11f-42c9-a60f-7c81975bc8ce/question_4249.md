# Q4249: system_instruction::checked_add - lamport arithmetic wraps in the withdraw path

## Question
Can an unprivileged attacker who submits durable-nonce system instructions against nonce accounts it created, using the nonce account as the transaction fee payer as well, drive `system_instruction::checked_add` to make checked_add or the withdraw balance computation wrap, so that the invariant that nonce lamport arithmetic is checked is broken and the outcome is Loss of Funds (lamport inflation / supply corruption)?

## Target
- File/function: `programs/system/src/system_instruction.rs` -> `checked_add`
- Entrypoint: submits durable-nonce system instructions against nonce accounts it created, using the nonce account as the transaction fee payer as well
- Attacker controls: the nonce account data and authority, the instruction variant, and the recent blockhash
- Exploit idea: Make checked_add or the withdraw balance computation wrap.
- Invariant to test: Nonce lamport arithmetic is checked.
- Expected Immunefi impact: Critical - Loss of Funds (lamport inflation / supply corruption)
- Fast validation: unit-test the nonce instruction against the crafted account state and assert authority and state checks reject it
