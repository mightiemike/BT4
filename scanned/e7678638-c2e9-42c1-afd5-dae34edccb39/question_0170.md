# Q170: signature_details::is_signature - is_signature classification drift (repeating the same precompile program id)

## Question
Can an unprivileged attacker who submits a transaction containing precompile instructions with attacker-authored data, repeating the same precompile program id across several instructions in one transaction, drive `signature_details::is_signature` to get an instruction classified as signature-bearing (or not) inconsistently between fee computation and execution, so that the invariant that one instruction has one classification across fee, cost and execution paths is broken and the outcome is Loss of Funds (fee, rent or reward undercharge draining protocol economics)?

## Target
- File/function: `runtime-transaction/src/signature_details.rs` -> `is_signature`
- Entrypoint: submits a transaction containing precompile instructions with attacker-authored data, repeating the same precompile program id across several instructions in one transaction
- Attacker controls: precompile program ids, the signature-count byte and every offset field inside precompile instruction data
- Exploit idea: Get an instruction classified as signature-bearing (or not) inconsistently between fee computation and execution.
- Invariant to test: One instruction has one classification across fee, cost and execution paths.
- Expected Immunefi impact: High - Loss of Funds (fee, rent or reward undercharge draining protocol economics)
- Fast validation: unit-test get_precompile_signature_details on the crafted instruction and assert the counted signatures equal what the precompile actually verifies
