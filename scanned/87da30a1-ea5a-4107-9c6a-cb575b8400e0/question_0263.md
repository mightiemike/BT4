# Q263: precompiles::get_precompiles - precompile dispatch matches the wrong program (placing the precompile instruction in a)

## Question
Can an unprivileged attacker who submits a transaction that invokes a precompile program directly or via a program that trusts the precompile result, placing the precompile instruction in a transaction that also carries a durable nonce advance, drive `precompiles::get_precompiles` to have get_precompile or is_precompile resolve an attacker-chosen program id to a precompile verifier, so that the invariant that precompile dispatch is an exact match on the canonical precompile program ids is broken and the outcome is Loss of Funds (theft of funds without the owner's signature)?

## Target
- File/function: `precompiles/src/lib.rs` -> `get_precompiles`
- Entrypoint: submits a transaction that invokes a precompile program directly or via a program that trusts the precompile result, placing the precompile instruction in a transaction that also carries a durable nonce advance
- Attacker controls: which precompile id is invoked, the precompile instruction data, and the surrounding instruction list
- Exploit idea: Have get_precompile or is_precompile resolve an attacker-chosen program id to a precompile verifier.
- Invariant to test: Precompile dispatch is an exact match on the canonical precompile program ids.
- Expected Immunefi impact: Critical - Loss of Funds (theft of funds without the owner's signature)
- Fast validation: unit-test verify_if_precompile with the crafted instruction and instruction_datas and assert an error is returned
