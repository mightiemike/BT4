# Q315: ed25519::Ed25519SignatureOffsets - cross-instruction offset points at attacker-mutable data (reusing the self-referential u16::MAX instruction index)

## Question
Can an unprivileged attacker who submits a transaction containing an ed25519 precompile instruction plus a program that acts on its success, reusing the self-referential u16::MAX instruction index so the offsets and message overlap, drive `ed25519::Ed25519SignatureOffsets` to resolve signature, pubkey or message bytes out of a different instruction whose contents the consuming program does not bind to, so that the invariant that the message a precompile verifies is the exact byte range the consuming program treats as authorized is broken and the outcome is Loss of Funds (theft of funds without the owner's signature)?

## Target
- File/function: `precompiles/src/ed25519.rs` -> `Ed25519SignatureOffsets`
- Entrypoint: submits a transaction containing an ed25519 precompile instruction plus a program that acts on its success, reusing the self-referential u16::MAX instruction index so the offsets and message overlap
- Attacker controls: the num_signatures byte, every Ed25519SignatureOffsets field, and the data of every other instruction the offsets can point into
- Exploit idea: Resolve signature, pubkey or message bytes out of a different instruction whose contents the consuming program does not bind to.
- Invariant to test: The message a precompile verifies is the exact byte range the consuming program treats as authorized.
- Expected Immunefi impact: Critical - Loss of Funds (theft of funds without the owner's signature)
- Fast validation: unit-test ed25519::verify with the crafted data and instruction_datas and assert PrecompileError is returned
