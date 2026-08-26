# Q395: secp256r1::verify - high-s signature accepted despite half-order check

## Question
Can an unprivileged attacker who submits a transaction containing a secp256r1 precompile instruction used as a passkey/WebAuthn authorization, using the precompile as a passkey authorization consumed by a program that only checks for success, drive `secp256r1::verify` to pass an s value that evades the SECP256R1_HALF_ORDER comparison so a malleable second encoding verifies, so that the invariant that exactly one (r,s) encoding verifies per authorization, with s <= n/2 is broken and the outcome is Loss of Funds (double-spend / replayed transfer)?

## Target
- File/function: `precompiles/src/secp256r1.rs` -> `verify`
- Entrypoint: submits a transaction containing a secp256r1 precompile instruction used as a passkey/WebAuthn authorization, using the precompile as a passkey authorization consumed by a program that only checks for success
- Attacker controls: num_signatures (bounded to 8), every Secp256r1SignatureOffsets field, the compressed pubkey bytes and the referenced message
- Exploit idea: Pass an s value that evades the SECP256R1_HALF_ORDER comparison so a malleable second encoding verifies.
- Invariant to test: Exactly one (r,s) encoding verifies per authorization, with s <= n/2.
- Expected Immunefi impact: Critical - Loss of Funds (double-spend / replayed transfer)
- Fast validation: unit-test secp256r1::verify and assert the crafted signature or point is rejected before OpenSSL verification
