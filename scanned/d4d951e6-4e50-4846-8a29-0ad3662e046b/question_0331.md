# Q331: ed25519::SIGNATURE_SERIALIZED_SIZE - verify_strict bypass via small-order or non-canonical key (reusing the self-referential u16::MAX instruction index)

## Question
Can an unprivileged attacker who submits a transaction containing an ed25519 precompile instruction plus a program that acts on its success, reusing the self-referential u16::MAX instruction index so the offsets and message overlap, drive `ed25519::SIGNATURE_SERIALIZED_SIZE` to supply a public key or signature encoding accepted by PublicKey::from_bytes but semantically ambiguous, so that the invariant that one signature encoding verifies for exactly one (key, message) pair is broken and the outcome is Loss of Funds (theft of funds without the owner's signature)?

## Target
- File/function: `precompiles/src/ed25519.rs` -> `SIGNATURE_SERIALIZED_SIZE`
- Entrypoint: submits a transaction containing an ed25519 precompile instruction plus a program that acts on its success, reusing the self-referential u16::MAX instruction index so the offsets and message overlap
- Attacker controls: the num_signatures byte, every Ed25519SignatureOffsets field, and the data of every other instruction the offsets can point into
- Exploit idea: Supply a public key or signature encoding accepted by PublicKey::from_bytes but semantically ambiguous.
- Invariant to test: One signature encoding verifies for exactly one (key, message) pair.
- Expected Immunefi impact: Critical - Loss of Funds (theft of funds without the owner's signature)
- Fast validation: unit-test ed25519::verify with the crafted data and instruction_datas and assert PrecompileError is returned
