# Q416: secp256r1::SIGNATURE_OFFSETS_SERIALIZED_SIZE - per-signature cost far below OpenSSL work

## Question
Can an unprivileged attacker who submits a transaction containing a secp256r1 precompile instruction used as a passkey/WebAuthn authorization, using the precompile as a passkey authorization consumed by a program that only checks for success, drive `secp256r1::SIGNATURE_OFFSETS_SERIALIZED_SIZE` to submit eight maximally expensive verifications for a fixed low fee, so that the invariant that charged cost is proportional to the elliptic-curve work performed is broken and the outcome is Liveness/Loss of Availability (block production degraded below usable capacity)?

## Target
- File/function: `precompiles/src/secp256r1.rs` -> `SIGNATURE_OFFSETS_SERIALIZED_SIZE`
- Entrypoint: submits a transaction containing a secp256r1 precompile instruction used as a passkey/WebAuthn authorization, using the precompile as a passkey authorization consumed by a program that only checks for success
- Attacker controls: num_signatures (bounded to 8), every Secp256r1SignatureOffsets field, the compressed pubkey bytes and the referenced message
- Exploit idea: Submit eight maximally expensive verifications for a fixed low fee.
- Invariant to test: Charged cost is proportional to the elliptic-curve work performed.
- Expected Immunefi impact: High - Liveness/Loss of Availability (block production degraded below usable capacity)
- Fast validation: unit-test secp256r1::verify and assert the crafted signature or point is rejected before OpenSSL verification
