# Q129: signature_details::get_num_signatures_in_instruction - counted signatures below verified signatures

## Question
Can an unprivileged attacker who submits a transaction containing precompile instructions with attacker-authored data, placing the precompile instruction after the instruction whose data its offsets reference, drive `signature_details::get_num_signatures_in_instruction` to report zero or few signatures for an instruction that forces many expensive verifications, so that the invariant that no transaction can force verification work it did not pay signature fees for is broken and the outcome is Liveness/Loss of Availability (block production degraded below usable capacity)?

## Target
- File/function: `runtime-transaction/src/signature_details.rs` -> `get_num_signatures_in_instruction`
- Entrypoint: submits a transaction containing precompile instructions with attacker-authored data, placing the precompile instruction after the instruction whose data its offsets reference
- Attacker controls: precompile program ids, the signature-count byte and every offset field inside precompile instruction data
- Exploit idea: Report zero or few signatures for an instruction that forces many expensive verifications.
- Invariant to test: No transaction can force verification work it did not pay signature fees for.
- Expected Immunefi impact: High - Liveness/Loss of Availability (block production degraded below usable capacity)
- Fast validation: unit-test get_precompile_signature_details on the crafted instruction and assert the counted signatures equal what the precompile actually verifies
