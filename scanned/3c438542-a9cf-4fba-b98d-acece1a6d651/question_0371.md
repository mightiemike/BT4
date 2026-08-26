# Q371: secp256k1::SecpSignatureOffsets - recovery id out of range or wrapping (supplying two signatures whose recovered addresses)

## Question
Can an unprivileged attacker who submits a transaction containing a secp256k1 precompile instruction consumed by an on-chain program, supplying two signatures whose recovered addresses are compared by the consuming program, drive `secp256k1::SecpSignatureOffsets` to supply a recovery id byte outside 0..=3 that is masked instead of rejected, yielding an attacker-chosen recovery, so that the invariant that only canonical recovery ids produce a recovered key is broken and the outcome is Loss of Funds (theft of funds without the owner's signature)?

## Target
- File/function: `precompiles/src/secp256k1.rs` -> `SecpSignatureOffsets`
- Entrypoint: submits a transaction containing a secp256k1 precompile instruction consumed by an on-chain program, supplying two signatures whose recovered addresses are compared by the consuming program
- Attacker controls: num_signatures, every SecpSignatureOffsets field, recovery id, and the referenced instruction data
- Exploit idea: Supply a recovery id byte outside 0..=3 that is masked instead of rejected, yielding an attacker-chosen recovery.
- Invariant to test: Only canonical recovery ids produce a recovered key.
- Expected Immunefi impact: Critical - Loss of Funds (theft of funds without the owner's signature)
- Fast validation: unit-test secp256k1::verify with the crafted data and assert the forged (eth_address, message) pair is rejected
