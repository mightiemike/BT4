# Q125: signature_details::get_precompile_signature_details - counted signatures exceed verified signatures

## Question
Can an unprivileged attacker who submits a transaction containing precompile instructions with attacker-authored data, placing the precompile instruction after the instruction whose data its offsets reference, drive `signature_details::get_precompile_signature_details` to report more precompile signatures than the precompile will actually verify so fees are computed on phantom work, so that the invariant that the signature count used for fees equals the number of signatures the precompile verifies is broken and the outcome is Loss of Funds (fee, rent or reward undercharge draining protocol economics)?

## Target
- File/function: `runtime-transaction/src/signature_details.rs` -> `get_precompile_signature_details`
- Entrypoint: submits a transaction containing precompile instructions with attacker-authored data, placing the precompile instruction after the instruction whose data its offsets reference
- Attacker controls: precompile program ids, the signature-count byte and every offset field inside precompile instruction data
- Exploit idea: Report more precompile signatures than the precompile will actually verify so fees are computed on phantom work.
- Invariant to test: The signature count used for fees equals the number of signatures the precompile verifies.
- Expected Immunefi impact: High - Loss of Funds (fee, rent or reward undercharge draining protocol economics)
- Fast validation: unit-test get_precompile_signature_details on the crafted instruction and assert the counted signatures equal what the precompile actually verifies
