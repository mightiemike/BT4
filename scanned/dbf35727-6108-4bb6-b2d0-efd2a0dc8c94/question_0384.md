# Q384: secp256k1::verify - empty message accepted (supplying two signatures whose recovered addresses)

## Question
Can an unprivileged attacker who submits a transaction containing a secp256k1 precompile instruction consumed by an on-chain program, supplying two signatures whose recovered addresses are compared by the consuming program, drive `secp256k1::verify` to verify a zero-length message so any recovered key trivially authorizes the consumer, so that the invariant that the verified message is non-empty and domain-separated is broken and the outcome is Loss of Funds (theft of funds without the owner's signature)?

## Target
- File/function: `precompiles/src/secp256k1.rs` -> `verify`
- Entrypoint: submits a transaction containing a secp256k1 precompile instruction consumed by an on-chain program, supplying two signatures whose recovered addresses are compared by the consuming program
- Attacker controls: num_signatures, every SecpSignatureOffsets field, recovery id, and the referenced instruction data
- Exploit idea: Verify a zero-length message so any recovered key trivially authorizes the consumer.
- Invariant to test: The verified message is non-empty and domain-separated.
- Expected Immunefi impact: Critical - Loss of Funds (theft of funds without the owner's signature)
- Fast validation: unit-test secp256k1::verify with the crafted data and assert the forged (eth_address, message) pair is rejected
