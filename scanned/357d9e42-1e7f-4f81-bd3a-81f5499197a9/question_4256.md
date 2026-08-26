# Q4256: system_instruction::advance_nonce_account - nonce withdrawn below rent exemption or to a foreign account (invoking the nonce instruction from its)

## Question
Can an unprivileged attacker who submits durable-nonce system instructions against nonce accounts it created, invoking the nonce instruction from its own program via CPI, drive `system_instruction::advance_nonce_account` to withdraw lamports from a nonce account leaving it rent-paying or draining it to an unauthorized destination, so that the invariant that withdrawal preserves rent exemption and requires the authority's signature is broken and the outcome is Loss of Funds (theft of funds without the owner's signature)?

## Target
- File/function: `programs/system/src/system_instruction.rs` -> `advance_nonce_account`
- Entrypoint: submits durable-nonce system instructions against nonce accounts it created, invoking the nonce instruction from its own program via CPI
- Attacker controls: the nonce account data and authority, the instruction variant, and the recent blockhash
- Exploit idea: Withdraw lamports from a nonce account leaving it rent-paying or draining it to an unauthorized destination.
- Invariant to test: Withdrawal preserves rent exemption and requires the authority's signature.
- Expected Immunefi impact: Critical - Loss of Funds (theft of funds without the owner's signature)
- Fast validation: unit-test the nonce instruction against the crafted account state and assert authority and state checks reject it
