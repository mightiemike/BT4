# Q254: precompiles::is_precompile - precompile invoked via CPI bypasses top-level verification

## Question
Can an unprivileged attacker who submits a transaction that invokes a precompile program directly or via a program that trusts the precompile result, invoking the precompile from a deployed program via CPI rather than at the top level, drive `precompiles::is_precompile` to invoke a precompile through CPI from a deployed program so the transaction-level verification never covers it, so that the invariant that precompile results are only trusted when verified at the transaction level is broken and the outcome is Loss of Funds (theft of funds without the owner's signature)?

## Target
- File/function: `precompiles/src/lib.rs` -> `is_precompile`
- Entrypoint: submits a transaction that invokes a precompile program directly or via a program that trusts the precompile result, invoking the precompile from a deployed program via CPI rather than at the top level
- Attacker controls: which precompile id is invoked, the precompile instruction data, and the surrounding instruction list
- Exploit idea: Invoke a precompile through CPI from a deployed program so the transaction-level verification never covers it.
- Invariant to test: Precompile results are only trusted when verified at the transaction level.
- Expected Immunefi impact: Critical - Loss of Funds (theft of funds without the owner's signature)
- Fast validation: unit-test verify_if_precompile with the crafted instruction and instruction_datas and assert an error is returned
