# Q239: precompiles::check_id - precompile dispatch matches the wrong program

## Question
Can an unprivileged attacker who submits a transaction that invokes a precompile program directly or via a program that trusts the precompile result, invoking the precompile from a deployed program via CPI rather than at the top level, drive `precompiles::check_id` to have get_precompile or is_precompile resolve an attacker-chosen program id to a precompile verifier, so that the invariant that precompile dispatch is an exact match on the canonical precompile program ids is broken and the outcome is Loss of Funds (theft of funds without the owner's signature)?

## Target
- File/function: `precompiles/src/lib.rs` -> `check_id`
- Entrypoint: submits a transaction that invokes a precompile program directly or via a program that trusts the precompile result, invoking the precompile from a deployed program via CPI rather than at the top level
- Attacker controls: which precompile id is invoked, the precompile instruction data, and the surrounding instruction list
- Exploit idea: Have get_precompile or is_precompile resolve an attacker-chosen program id to a precompile verifier.
- Invariant to test: Precompile dispatch is an exact match on the canonical precompile program ids.
- Expected Immunefi impact: Critical - Loss of Funds (theft of funds without the owner's signature)
- Fast validation: unit-test verify_if_precompile with the crafted instruction and instruction_datas and assert an error is returned
