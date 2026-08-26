# Q325: ed25519::verify - message_data_size zero or wrapping (reusing the self-referential u16::MAX instruction index)

## Question
Can an unprivileged attacker who submits a transaction containing an ed25519 precompile instruction plus a program that acts on its success, reusing the self-referential u16::MAX instruction index so the offsets and message overlap, drive `ed25519::verify` to set message_data_size so start.saturating_add(size) collapses and an empty or unintended message is verified, so that the invariant that the verified message length is exactly the length the signer signed is broken and the outcome is Loss of Funds (theft of funds without the owner's signature)?

## Target
- File/function: `precompiles/src/ed25519.rs` -> `verify`
- Entrypoint: submits a transaction containing an ed25519 precompile instruction plus a program that acts on its success, reusing the self-referential u16::MAX instruction index so the offsets and message overlap
- Attacker controls: the num_signatures byte, every Ed25519SignatureOffsets field, and the data of every other instruction the offsets can point into
- Exploit idea: Set message_data_size so start.saturating_add(size) collapses and an empty or unintended message is verified.
- Invariant to test: The verified message length is exactly the length the signer signed.
- Expected Immunefi impact: Critical - Loss of Funds (theft of funds without the owner's signature)
- Fast validation: unit-test ed25519::verify with the crafted data and instruction_datas and assert PrecompileError is returned
