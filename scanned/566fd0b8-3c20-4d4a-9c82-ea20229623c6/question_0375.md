# Q375: secp256k1::eth_address_from_pubkey - eth_address_from_pubkey truncation collision (supplying two signatures whose recovered addresses)

## Question
Can an unprivileged attacker who submits a transaction containing a secp256k1 precompile instruction consumed by an on-chain program, supplying two signatures whose recovered addresses are compared by the consuming program, drive `secp256k1::eth_address_from_pubkey` to exploit hashed-pubkey truncation so a second key maps to the victim's stored eth address, so that the invariant that an eth address identifies exactly one recoverable public key in practice is broken and the outcome is Loss of Funds (theft of funds without the owner's signature)?

## Target
- File/function: `precompiles/src/secp256k1.rs` -> `eth_address_from_pubkey`
- Entrypoint: submits a transaction containing a secp256k1 precompile instruction consumed by an on-chain program, supplying two signatures whose recovered addresses are compared by the consuming program
- Attacker controls: num_signatures, every SecpSignatureOffsets field, recovery id, and the referenced instruction data
- Exploit idea: Exploit hashed-pubkey truncation so a second key maps to the victim's stored eth address.
- Invariant to test: An eth address identifies exactly one recoverable public key in practice.
- Expected Immunefi impact: Critical - Loss of Funds (theft of funds without the owner's signature)
- Fast validation: unit-test secp256k1::verify with the crafted data and assert the forged (eth_address, message) pair is rejected
