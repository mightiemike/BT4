# Q343: ed25519::SIGNATURE_OFFSETS_SERIALIZED_SIZE - num_signatures byte disagrees with the offsets array (declaring several signatures where only the)

## Question
Can an unprivileged attacker who submits a transaction containing an ed25519 precompile instruction plus a program that acts on its success, declaring several signatures where only the first is well formed, drive `ed25519::SIGNATURE_OFFSETS_SERIALIZED_SIZE` to declare a num_signatures value whose expected_data_size check passes while some offsets structs are read from attacker padding, so that the invariant that num_signatures exactly describes how many complete offsets structs the data contains is broken and the outcome is Loss of Funds (theft of funds without the owner's signature)?

## Target
- File/function: `precompiles/src/ed25519.rs` -> `SIGNATURE_OFFSETS_SERIALIZED_SIZE`
- Entrypoint: submits a transaction containing an ed25519 precompile instruction plus a program that acts on its success, declaring several signatures where only the first is well formed
- Attacker controls: the num_signatures byte, every Ed25519SignatureOffsets field, and the data of every other instruction the offsets can point into
- Exploit idea: Declare a num_signatures value whose expected_data_size check passes while some offsets structs are read from attacker padding.
- Invariant to test: Num_signatures exactly describes how many complete offsets structs the data contains.
- Expected Immunefi impact: Critical - Loss of Funds (theft of funds without the owner's signature)
- Fast validation: unit-test ed25519::verify with the crafted data and instruction_datas and assert PrecompileError is returned
