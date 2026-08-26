# Q312: ed25519::verify - offsets pointing into a later instruction not yet fixed

## Question
Can an unprivileged attacker who submits a transaction containing an ed25519 precompile instruction plus a program that acts on its success, pointing the offsets at the instruction data of a second instruction the attacker also controls, drive `ed25519::verify` to reference an instruction whose data is rewritten by an earlier instruction in the same transaction before the consumer reads it, so that the invariant that instruction data verified by a precompile is immutable for the whole transaction is broken and the outcome is Loss of Funds (theft of funds without the owner's signature)?

## Target
- File/function: `precompiles/src/ed25519.rs` -> `verify`
- Entrypoint: submits a transaction containing an ed25519 precompile instruction plus a program that acts on its success, pointing the offsets at the instruction data of a second instruction the attacker also controls
- Attacker controls: the num_signatures byte, every Ed25519SignatureOffsets field, and the data of every other instruction the offsets can point into
- Exploit idea: Reference an instruction whose data is rewritten by an earlier instruction in the same transaction before the consumer reads it.
- Invariant to test: Instruction data verified by a precompile is immutable for the whole transaction.
- Expected Immunefi impact: Critical - Loss of Funds (theft of funds without the owner's signature)
- Fast validation: unit-test ed25519::verify with the crafted data and instruction_datas and assert PrecompileError is returned
