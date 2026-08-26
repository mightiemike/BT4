# Q420: secp256r1::verify - offset arithmetic collapse yields empty message

## Question
Can an unprivileged attacker who submits a transaction containing a secp256r1 precompile instruction used as a passkey/WebAuthn authorization, using the precompile as a passkey authorization consumed by a program that only checks for success, drive `secp256r1::verify` to set message_data_offset and size so the resolved slice is empty yet verification succeeds, so that the invariant that an empty or attacker-truncated message never yields a successful verification is broken and the outcome is Loss of Funds (theft of funds without the owner's signature)?

## Target
- File/function: `precompiles/src/secp256r1.rs` -> `verify`
- Entrypoint: submits a transaction containing a secp256r1 precompile instruction used as a passkey/WebAuthn authorization, using the precompile as a passkey authorization consumed by a program that only checks for success
- Attacker controls: num_signatures (bounded to 8), every Secp256r1SignatureOffsets field, the compressed pubkey bytes and the referenced message
- Exploit idea: Set message_data_offset and size so the resolved slice is empty yet verification succeeds.
- Invariant to test: An empty or attacker-truncated message never yields a successful verification.
- Expected Immunefi impact: Critical - Loss of Funds (theft of funds without the owner's signature)
- Fast validation: unit-test secp256r1::verify and assert the crafted signature or point is rejected before OpenSSL verification
