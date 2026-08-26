# Q252: precompiles::verify - error swallowed and treated as success

## Question
Can an unprivileged attacker who submits a transaction that invokes a precompile program directly or via a program that trusts the precompile result, invoking the precompile from a deployed program via CPI rather than at the top level, drive `precompiles::verify` to cause a precompile verification error to be mapped to Ok so a forged signature is accepted, so that the invariant that any precompile verification failure aborts the whole transaction is broken and the outcome is Loss of Funds (theft of funds without the owner's signature)?

## Target
- File/function: `precompiles/src/lib.rs` -> `verify`
- Entrypoint: submits a transaction that invokes a precompile program directly or via a program that trusts the precompile result, invoking the precompile from a deployed program via CPI rather than at the top level
- Attacker controls: which precompile id is invoked, the precompile instruction data, and the surrounding instruction list
- Exploit idea: Cause a precompile verification error to be mapped to Ok so a forged signature is accepted.
- Invariant to test: Any precompile verification failure aborts the whole transaction.
- Expected Immunefi impact: Critical - Loss of Funds (theft of funds without the owner's signature)
- Fast validation: unit-test verify_if_precompile with the crafted instruction and instruction_datas and assert an error is returned
