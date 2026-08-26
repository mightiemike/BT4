# Q132: signature_details::check_program_id - program id spoofed to a non-precompile

## Question
Can an unprivileged attacker who submits a transaction containing precompile instructions with attacker-authored data, placing the precompile instruction after the instruction whose data its offsets reference, drive `signature_details::check_program_id` to route a non-precompile program id through the precompile counting branch, or hide a precompile behind an id check that fails open, so that the invariant that signature counting is keyed on the canonical precompile ids and nothing else is broken and the outcome is Loss of Funds (fee, rent or reward undercharge draining protocol economics)?

## Target
- File/function: `runtime-transaction/src/signature_details.rs` -> `check_program_id`
- Entrypoint: submits a transaction containing precompile instructions with attacker-authored data, placing the precompile instruction after the instruction whose data its offsets reference
- Attacker controls: precompile program ids, the signature-count byte and every offset field inside precompile instruction data
- Exploit idea: Route a non-precompile program id through the precompile counting branch, or hide a precompile behind an id check that fails open.
- Invariant to test: Signature counting is keyed on the canonical precompile ids and nothing else.
- Expected Immunefi impact: High - Loss of Funds (fee, rent or reward undercharge draining protocol economics)
- Fast validation: unit-test get_precompile_signature_details on the crafted instruction and assert the counted signatures equal what the precompile actually verifies
