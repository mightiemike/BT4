# Q367: secp256k1::verify - recovered eth address bound to the wrong message (supplying two signatures whose recovered addresses)

## Question
Can an unprivileged attacker who submits a transaction containing a secp256k1 precompile instruction consumed by an on-chain program, supplying two signatures whose recovered addresses are compared by the consuming program, drive `secp256k1::verify` to recover a public key over a message range that is not the range the consuming program authorizes, so that the invariant that the recovered eth address authorizes exactly the bytes the program acts on is broken and the outcome is Loss of Funds (theft of funds without the owner's signature)?

## Target
- File/function: `precompiles/src/secp256k1.rs` -> `verify`
- Entrypoint: submits a transaction containing a secp256k1 precompile instruction consumed by an on-chain program, supplying two signatures whose recovered addresses are compared by the consuming program
- Attacker controls: num_signatures, every SecpSignatureOffsets field, recovery id, and the referenced instruction data
- Exploit idea: Recover a public key over a message range that is not the range the consuming program authorizes.
- Invariant to test: The recovered eth address authorizes exactly the bytes the program acts on.
- Expected Immunefi impact: Critical - Loss of Funds (theft of funds without the owner's signature)
- Fast validation: unit-test secp256k1::verify with the crafted data and assert the forged (eth_address, message) pair is rejected
