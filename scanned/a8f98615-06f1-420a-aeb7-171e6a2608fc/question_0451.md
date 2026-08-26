# Q451: secp256r1::SECP256R1_ORDER_MINUS_ONE - r or s outside [1, n-1] accepted (referencing the compressed pubkey from a)

## Question
Can an unprivileged attacker who submits a transaction containing a secp256r1 precompile instruction used as a passkey/WebAuthn authorization, referencing the compressed pubkey from a different instruction's data, drive `secp256r1::SECP256R1_ORDER_MINUS_ONE` to supply r or s equal to zero or above order-1 so the range comparison against SECP256R1_ORDER_MINUS_ONE fails open, so that the invariant that r and s are both strictly inside the scalar range before verification is broken and the outcome is Loss of Funds (theft of funds without the owner's signature)?

## Target
- File/function: `precompiles/src/secp256r1.rs` -> `SECP256R1_ORDER_MINUS_ONE`
- Entrypoint: submits a transaction containing a secp256r1 precompile instruction used as a passkey/WebAuthn authorization, referencing the compressed pubkey from a different instruction's data
- Attacker controls: num_signatures (bounded to 8), every Secp256r1SignatureOffsets field, the compressed pubkey bytes and the referenced message
- Exploit idea: Supply r or s equal to zero or above order-1 so the range comparison against SECP256R1_ORDER_MINUS_ONE fails open.
- Invariant to test: R and s are both strictly inside the scalar range before verification.
- Expected Immunefi impact: Critical - Loss of Funds (theft of funds without the owner's signature)
- Fast validation: unit-test secp256r1::verify and assert the crafted signature or point is rejected before OpenSSL verification
