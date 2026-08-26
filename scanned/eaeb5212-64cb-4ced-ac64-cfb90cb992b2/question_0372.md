# Q372: secp256k1::verify - signature malleability via high-s (supplying two signatures whose recovered addresses)

## Question
Can an unprivileged attacker who submits a transaction containing a secp256k1 precompile instruction consumed by an on-chain program, supplying two signatures whose recovered addresses are compared by the consuming program, drive `secp256k1::verify` to submit both s and order-s forms so the same authorization is accepted twice under different bytes, so that the invariant that each authorization has exactly one accepted signature encoding is broken and the outcome is Loss of Funds (double-spend / replayed transfer)?

## Target
- File/function: `precompiles/src/secp256k1.rs` -> `verify`
- Entrypoint: submits a transaction containing a secp256k1 precompile instruction consumed by an on-chain program, supplying two signatures whose recovered addresses are compared by the consuming program
- Attacker controls: num_signatures, every SecpSignatureOffsets field, recovery id, and the referenced instruction data
- Exploit idea: Submit both s and order-s forms so the same authorization is accepted twice under different bytes.
- Invariant to test: Each authorization has exactly one accepted signature encoding.
- Expected Immunefi impact: Critical - Loss of Funds (double-spend / replayed transfer)
- Fast validation: unit-test secp256k1::verify with the crafted data and assert the forged (eth_address, message) pair is rejected
