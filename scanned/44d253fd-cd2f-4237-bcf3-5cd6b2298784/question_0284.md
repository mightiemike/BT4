# Q284: precompiles::is_precompile - precompile dispatch matches the wrong program (submitting in the slot where the)

## Question
Can an unprivileged attacker who submits a transaction that invokes a precompile program directly or via a program that trusts the precompile result, submitting in the slot where the precompile's feature gate flips, drive `precompiles::is_precompile` to have get_precompile or is_precompile resolve an attacker-chosen program id to a precompile verifier, so that the invariant that precompile dispatch is an exact match on the canonical precompile program ids is broken and the outcome is Loss of Funds (theft of funds without the owner's signature)?

## Target
- File/function: `precompiles/src/lib.rs` -> `is_precompile`
- Entrypoint: submits a transaction that invokes a precompile program directly or via a program that trusts the precompile result, submitting in the slot where the precompile's feature gate flips
- Attacker controls: which precompile id is invoked, the precompile instruction data, and the surrounding instruction list
- Exploit idea: Have get_precompile or is_precompile resolve an attacker-chosen program id to a precompile verifier.
- Invariant to test: Precompile dispatch is an exact match on the canonical precompile program ids.
- Expected Immunefi impact: Critical - Loss of Funds (theft of funds without the owner's signature)
- Fast validation: unit-test verify_if_precompile with the crafted instruction and instruction_datas and assert an error is returned
