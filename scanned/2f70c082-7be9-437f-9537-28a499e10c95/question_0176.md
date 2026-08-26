# Q176: signature_details::build - counted signatures exceed verified signatures (setting the precompile signature-count byte to)

## Question
Can an unprivileged attacker who submits a transaction containing precompile instructions with attacker-authored data, setting the precompile signature-count byte to 255 while supplying a single offsets struct, drive `signature_details::build` to report more precompile signatures than the precompile will actually verify so fees are computed on phantom work, so that the invariant that the signature count used for fees equals the number of signatures the precompile verifies is broken and the outcome is Loss of Funds (fee, rent or reward undercharge draining protocol economics)?

## Target
- File/function: `runtime-transaction/src/signature_details.rs` -> `build`
- Entrypoint: submits a transaction containing precompile instructions with attacker-authored data, setting the precompile signature-count byte to 255 while supplying a single offsets struct
- Attacker controls: precompile program ids, the signature-count byte and every offset field inside precompile instruction data
- Exploit idea: Report more precompile signatures than the precompile will actually verify so fees are computed on phantom work.
- Invariant to test: The signature count used for fees equals the number of signatures the precompile verifies.
- Expected Immunefi impact: High - Loss of Funds (fee, rent or reward undercharge draining protocol economics)
- Fast validation: unit-test get_precompile_signature_details on the crafted instruction and assert the counted signatures equal what the precompile actually verifies
