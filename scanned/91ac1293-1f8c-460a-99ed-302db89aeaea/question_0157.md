# Q157: signature_details::is_signature - program id spoofed to a non-precompile (repeating the same precompile program id)

## Question
Can an unprivileged attacker who submits a transaction containing precompile instructions with attacker-authored data, repeating the same precompile program id across several instructions in one transaction, drive `signature_details::is_signature` to route a non-precompile program id through the precompile counting branch, or hide a precompile behind an id check that fails open, so that the invariant that signature counting is keyed on the canonical precompile ids and nothing else is broken and the outcome is Loss of Funds (fee, rent or reward undercharge draining protocol economics)?

## Target
- File/function: `runtime-transaction/src/signature_details.rs` -> `is_signature`
- Entrypoint: submits a transaction containing precompile instructions with attacker-authored data, repeating the same precompile program id across several instructions in one transaction
- Attacker controls: precompile program ids, the signature-count byte and every offset field inside precompile instruction data
- Exploit idea: Route a non-precompile program id through the precompile counting branch, or hide a precompile behind an id check that fails open.
- Invariant to test: Signature counting is keyed on the canonical precompile ids and nothing else.
- Expected Immunefi impact: High - Loss of Funds (fee, rent or reward undercharge draining protocol economics)
- Fast validation: unit-test get_precompile_signature_details on the crafted instruction and assert the counted signatures equal what the precompile actually verifies
