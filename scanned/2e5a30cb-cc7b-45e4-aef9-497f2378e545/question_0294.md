# Q294: ed25519::verify - zero-signature instruction reported as verified

## Question
Can an unprivileged attacker who submits a transaction containing an ed25519 precompile instruction plus a program that acts on its success, pointing the offsets at the instruction data of a second instruction the attacker also controls, drive `ed25519::verify` to get verify to return Ok with no signature actually checked, so a program that only checks 'precompile instruction present' is fooled, so that the invariant that an Ok result from the precompile implies at least one real signature was verified is broken and the outcome is Loss of Funds (theft of funds without the owner's signature)?

## Target
- File/function: `precompiles/src/ed25519.rs` -> `verify`
- Entrypoint: submits a transaction containing an ed25519 precompile instruction plus a program that acts on its success, pointing the offsets at the instruction data of a second instruction the attacker also controls
- Attacker controls: the num_signatures byte, every Ed25519SignatureOffsets field, and the data of every other instruction the offsets can point into
- Exploit idea: Get verify to return Ok with no signature actually checked, so a program that only checks 'precompile instruction present' is fooled.
- Invariant to test: An Ok result from the precompile implies at least one real signature was verified.
- Expected Immunefi impact: Critical - Loss of Funds (theft of funds without the owner's signature)
- Fast validation: unit-test ed25519::verify with the crafted data and instruction_datas and assert PrecompileError is returned
