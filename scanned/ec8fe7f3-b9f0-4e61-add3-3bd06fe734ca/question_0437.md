# Q437: secp256r1::FIELD_SIZE - OpenSSL error mapped to success (submitting the maximum eight offsets structs)

## Question
Can an unprivileged attacker who submits a transaction containing a secp256r1 precompile instruction used as a passkey/WebAuthn authorization, submitting the maximum eight offsets structs in a single instruction, drive `secp256r1::FIELD_SIZE` to cause a BigNum, EcGroup or Verifier construction failure to be treated as a passing verification, so that the invariant that every OpenSSL failure maps to a precompile error, never to Ok is broken and the outcome is Loss of Funds (theft of funds without the owner's signature)?

## Target
- File/function: `precompiles/src/secp256r1.rs` -> `FIELD_SIZE`
- Entrypoint: submits a transaction containing a secp256r1 precompile instruction used as a passkey/WebAuthn authorization, submitting the maximum eight offsets structs in a single instruction
- Attacker controls: num_signatures (bounded to 8), every Secp256r1SignatureOffsets field, the compressed pubkey bytes and the referenced message
- Exploit idea: Cause a BigNum, EcGroup or Verifier construction failure to be treated as a passing verification.
- Invariant to test: Every OpenSSL failure maps to a precompile error, never to Ok.
- Expected Immunefi impact: Critical - Loss of Funds (theft of funds without the owner's signature)
- Fast validation: unit-test secp256r1::verify and assert the crafted signature or point is rejected before OpenSSL verification
