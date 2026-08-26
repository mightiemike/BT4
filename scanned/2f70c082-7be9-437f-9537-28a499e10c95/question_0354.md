# Q354: secp256k1::get_data_slice - cross-instruction data resolution out of bounds

## Question
Can an unprivileged attacker who submits a transaction containing a secp256k1 precompile instruction consumed by an on-chain program, having an on-chain program read the precompile instruction back through the instructions sysvar, drive `secp256k1::get_data_slice` to point eth_address_instruction_index or message_instruction_index at an index the bounds check does not reject, so that the invariant that every referenced instruction index is strictly less than instruction_datas.len() is broken and the outcome is Liveness/Loss of Availability (cluster halt requiring human intervention)?

## Target
- File/function: `precompiles/src/secp256k1.rs` -> `get_data_slice`
- Entrypoint: submits a transaction containing a secp256k1 precompile instruction consumed by an on-chain program, having an on-chain program read the precompile instruction back through the instructions sysvar
- Attacker controls: num_signatures, every SecpSignatureOffsets field, recovery id, and the referenced instruction data
- Exploit idea: Point eth_address_instruction_index or message_instruction_index at an index the bounds check does not reject.
- Invariant to test: Every referenced instruction index is strictly less than instruction_datas.len().
- Expected Immunefi impact: Critical - Liveness/Loss of Availability (cluster halt requiring human intervention)
- Fast validation: unit-test secp256k1::verify with the crafted data and assert the forged (eth_address, message) pair is rejected
