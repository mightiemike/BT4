# Q438: secp256r1::verify - unaligned offsets read past validated length (submitting the maximum eight offsets structs)

## Question
Can an unprivileged attacker who submits a transaction containing a secp256r1 precompile instruction used as a passkey/WebAuthn authorization, submitting the maximum eight offsets structs in a single instruction, drive `secp256r1::verify` to make read_unaligned of Secp256r1SignatureOffsets read attacker padding beyond the length check, so that the invariant that every unaligned struct read is covered by the preceding size check is broken and the outcome is Liveness/Loss of Availability (cluster halt requiring human intervention)?

## Target
- File/function: `precompiles/src/secp256r1.rs` -> `verify`
- Entrypoint: submits a transaction containing a secp256r1 precompile instruction used as a passkey/WebAuthn authorization, submitting the maximum eight offsets structs in a single instruction
- Attacker controls: num_signatures (bounded to 8), every Secp256r1SignatureOffsets field, the compressed pubkey bytes and the referenced message
- Exploit idea: Make read_unaligned of Secp256r1SignatureOffsets read attacker padding beyond the length check.
- Invariant to test: Every unaligned struct read is covered by the preceding size check.
- Expected Immunefi impact: Critical - Liveness/Loss of Availability (cluster halt requiring human intervention)
- Fast validation: unit-test secp256r1::verify and assert the crafted signature or point is rejected before OpenSSL verification
