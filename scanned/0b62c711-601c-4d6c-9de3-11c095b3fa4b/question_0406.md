# Q406: secp256r1::Secp256r1SignatureOffsets - message resolved from an attacker-mutable instruction

## Question
Can an unprivileged attacker who submits a transaction containing a secp256r1 precompile instruction used as a passkey/WebAuthn authorization, using the precompile as a passkey authorization consumed by a program that only checks for success, drive `secp256r1::Secp256r1SignatureOffsets` to point message_instruction_index at data that differs from what the consuming program authorizes, so that the invariant that the verified message equals the authorized payload byte for byte is broken and the outcome is Loss of Funds (theft of funds without the owner's signature)?

## Target
- File/function: `precompiles/src/secp256r1.rs` -> `Secp256r1SignatureOffsets`
- Entrypoint: submits a transaction containing a secp256r1 precompile instruction used as a passkey/WebAuthn authorization, using the precompile as a passkey authorization consumed by a program that only checks for success
- Attacker controls: num_signatures (bounded to 8), every Secp256r1SignatureOffsets field, the compressed pubkey bytes and the referenced message
- Exploit idea: Point message_instruction_index at data that differs from what the consuming program authorizes.
- Invariant to test: The verified message equals the authorized payload byte for byte.
- Expected Immunefi impact: Critical - Loss of Funds (theft of funds without the owner's signature)
- Fast validation: unit-test secp256r1::verify and assert the crafted signature or point is rejected before OpenSSL verification
