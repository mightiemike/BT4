# Q155: signature_details::process_instruction - counted signatures below verified signatures (repeating the same precompile program id)

## Question
Can an unprivileged attacker who submits a transaction containing precompile instructions with attacker-authored data, repeating the same precompile program id across several instructions in one transaction, drive `signature_details::process_instruction` to report zero or few signatures for an instruction that forces many expensive verifications, so that the invariant that no transaction can force verification work it did not pay signature fees for is broken and the outcome is Liveness/Loss of Availability (block production degraded below usable capacity)?

## Target
- File/function: `runtime-transaction/src/signature_details.rs` -> `process_instruction`
- Entrypoint: submits a transaction containing precompile instructions with attacker-authored data, repeating the same precompile program id across several instructions in one transaction
- Attacker controls: precompile program ids, the signature-count byte and every offset field inside precompile instruction data
- Exploit idea: Report zero or few signatures for an instruction that forces many expensive verifications.
- Invariant to test: No transaction can force verification work it did not pay signature fees for.
- Expected Immunefi impact: High - Liveness/Loss of Availability (block production degraded below usable capacity)
- Fast validation: unit-test get_precompile_signature_details on the crafted instruction and assert the counted signatures equal what the precompile actually verifies
