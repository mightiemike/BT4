# Q301: ed25519::verify - unaligned POD read past the checked bound

## Question
Can an unprivileged attacker who submits a transaction containing an ed25519 precompile instruction plus a program that acts on its success, pointing the offsets at the instruction data of a second instruction the attacker also controls, drive `ed25519::verify` to make read_unaligned of Ed25519SignatureOffsets read bytes beyond what the expected_data_size check guaranteed, so that the invariant that every unsafe unaligned read is fully covered by a preceding length check is broken and the outcome is Liveness/Loss of Availability (cluster halt requiring human intervention)?

## Target
- File/function: `precompiles/src/ed25519.rs` -> `verify`
- Entrypoint: submits a transaction containing an ed25519 precompile instruction plus a program that acts on its success, pointing the offsets at the instruction data of a second instruction the attacker also controls
- Attacker controls: the num_signatures byte, every Ed25519SignatureOffsets field, and the data of every other instruction the offsets can point into
- Exploit idea: Make read_unaligned of Ed25519SignatureOffsets read bytes beyond what the expected_data_size check guaranteed.
- Invariant to test: Every unsafe unaligned read is fully covered by a preceding length check.
- Expected Immunefi impact: Critical - Liveness/Loss of Availability (cluster halt requiring human intervention)
- Fast validation: unit-test ed25519::verify with the crafted data and instruction_datas and assert PrecompileError is returned
