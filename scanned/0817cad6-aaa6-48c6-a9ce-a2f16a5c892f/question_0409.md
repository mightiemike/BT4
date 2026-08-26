# Q409: secp256r1::Secp256r1SignatureOffsets - num_signatures bound of 8 evaded

## Question
Can an unprivileged attacker who submits a transaction containing a secp256r1 precompile instruction used as a passkey/WebAuthn authorization, using the precompile as a passkey authorization consumed by a program that only checks for success, drive `secp256r1::Secp256r1SignatureOffsets` to encode more offsets structs than the eight-signature bound while passing expected_data_size, so that the invariant that at most eight complete offsets structs are ever parsed is broken and the outcome is Liveness/Loss of Availability (cluster halt requiring human intervention)?

## Target
- File/function: `precompiles/src/secp256r1.rs` -> `Secp256r1SignatureOffsets`
- Entrypoint: submits a transaction containing a secp256r1 precompile instruction used as a passkey/WebAuthn authorization, using the precompile as a passkey authorization consumed by a program that only checks for success
- Attacker controls: num_signatures (bounded to 8), every Secp256r1SignatureOffsets field, the compressed pubkey bytes and the referenced message
- Exploit idea: Encode more offsets structs than the eight-signature bound while passing expected_data_size.
- Invariant to test: At most eight complete offsets structs are ever parsed.
- Expected Immunefi impact: Critical - Liveness/Loss of Availability (cluster halt requiring human intervention)
- Fast validation: unit-test secp256r1::verify and assert the crafted signature or point is rejected before OpenSSL verification
