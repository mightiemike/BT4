# Q385: secp256k1::verify - feature-gated extra checks diverge across nodes (supplying two signatures whose recovered addresses)

## Question
Can an unprivileged attacker who submits a transaction containing a secp256k1 precompile instruction consumed by an on-chain program, supplying two signatures whose recovered addresses are compared by the consuming program, drive `secp256k1::verify` to invoke the precompile at the slot where its additional checks activate so nodes disagree on validity, so that the invariant that precompile semantics are identical on every node at a given slot is broken and the outcome is Consensus/Safety Violation (bank hash divergence or fork)?

## Target
- File/function: `precompiles/src/secp256k1.rs` -> `verify`
- Entrypoint: submits a transaction containing a secp256k1 precompile instruction consumed by an on-chain program, supplying two signatures whose recovered addresses are compared by the consuming program
- Attacker controls: num_signatures, every SecpSignatureOffsets field, recovery id, and the referenced instruction data
- Exploit idea: Invoke the precompile at the slot where its additional checks activate so nodes disagree on validity.
- Invariant to test: Precompile semantics are identical on every node at a given slot.
- Expected Immunefi impact: Critical - Consensus/Safety Violation (bank hash divergence or fork)
- Fast validation: unit-test secp256k1::verify with the crafted data and assert the forged (eth_address, message) pair is rejected
