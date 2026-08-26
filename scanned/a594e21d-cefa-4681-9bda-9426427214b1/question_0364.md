# Q364: secp256k1::SIGNATURE_OFFSETS_SERIALIZED_SIZE - verification work unpaid

## Question
Can an unprivileged attacker who submits a transaction containing a secp256k1 precompile instruction consumed by an on-chain program, having an on-chain program read the precompile instruction back through the instructions sysvar, drive `secp256k1::SIGNATURE_OFFSETS_SERIALIZED_SIZE` to force the maximum number of ecrecover operations in one instruction relative to the fee charged, so that the invariant that signature fees scale with the number of recoveries performed is broken and the outcome is Liveness/Loss of Availability (block production degraded below usable capacity)?

## Target
- File/function: `precompiles/src/secp256k1.rs` -> `SIGNATURE_OFFSETS_SERIALIZED_SIZE`
- Entrypoint: submits a transaction containing a secp256k1 precompile instruction consumed by an on-chain program, having an on-chain program read the precompile instruction back through the instructions sysvar
- Attacker controls: num_signatures, every SecpSignatureOffsets field, recovery id, and the referenced instruction data
- Exploit idea: Force the maximum number of ecrecover operations in one instruction relative to the fee charged.
- Invariant to test: Signature fees scale with the number of recoveries performed.
- Expected Immunefi impact: High - Liveness/Loss of Availability (block production degraded below usable capacity)
- Fast validation: unit-test secp256k1::verify with the crafted data and assert the forged (eth_address, message) pair is rejected
