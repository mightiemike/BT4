# Q443: secp256r1::verify - data[1] unchecked byte creates duplicate encodings (submitting the maximum eight offsets structs)

## Question
Can an unprivileged attacker who submits a transaction containing a secp256r1 precompile instruction used as a passkey/WebAuthn authorization, submitting the maximum eight offsets structs in a single instruction, drive `secp256r1::verify` to use the ignored byte to produce two byte-distinct instructions with identical authorization semantics, so that the invariant that each authorization has a single canonical encoding for replay protection is broken and the outcome is Loss of Funds (double-spend / replayed transfer)?

## Target
- File/function: `precompiles/src/secp256r1.rs` -> `verify`
- Entrypoint: submits a transaction containing a secp256r1 precompile instruction used as a passkey/WebAuthn authorization, submitting the maximum eight offsets structs in a single instruction
- Attacker controls: num_signatures (bounded to 8), every Secp256r1SignatureOffsets field, the compressed pubkey bytes and the referenced message
- Exploit idea: Use the ignored byte to produce two byte-distinct instructions with identical authorization semantics.
- Invariant to test: Each authorization has a single canonical encoding for replay protection.
- Expected Immunefi impact: Critical - Loss of Funds (double-spend / replayed transfer)
- Fast validation: unit-test secp256r1::verify and assert the crafted signature or point is rejected before OpenSSL verification
