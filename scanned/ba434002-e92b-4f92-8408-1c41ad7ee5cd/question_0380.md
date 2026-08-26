# Q380: secp256k1::verify - num_signatures vs data length mismatch (supplying two signatures whose recovered addresses)

## Question
Can an unprivileged attacker who submits a transaction containing a secp256k1 precompile instruction consumed by an on-chain program, supplying two signatures whose recovered addresses are compared by the consuming program, drive `secp256k1::verify` to declare a signature count whose offsets structs extend past the validated data length, so that the invariant that the data length check covers every offsets struct that is read is broken and the outcome is Liveness/Loss of Availability (cluster halt requiring human intervention)?

## Target
- File/function: `precompiles/src/secp256k1.rs` -> `verify`
- Entrypoint: submits a transaction containing a secp256k1 precompile instruction consumed by an on-chain program, supplying two signatures whose recovered addresses are compared by the consuming program
- Attacker controls: num_signatures, every SecpSignatureOffsets field, recovery id, and the referenced instruction data
- Exploit idea: Declare a signature count whose offsets structs extend past the validated data length.
- Invariant to test: The data length check covers every offsets struct that is read.
- Expected Immunefi impact: Critical - Liveness/Loss of Availability (cluster halt requiring human intervention)
- Fast validation: unit-test secp256k1::verify with the crafted data and assert the forged (eth_address, message) pair is rejected
