# Q265: precompiles::verify - verification skipped when the precompile is not the top-level program (placing the precompile instruction in a)

## Question
Can an unprivileged attacker who submits a transaction that invokes a precompile program directly or via a program that trusts the precompile result, placing the precompile instruction in a transaction that also carries a durable nonce advance, drive `precompiles::verify` to reach execution with a precompile instruction that was never verified because it was invoked from an unexpected position, so that the invariant that every precompile instruction in a transaction is verified before any instruction executes is broken and the outcome is Loss of Funds (theft of funds without the owner's signature)?

## Target
- File/function: `precompiles/src/lib.rs` -> `verify`
- Entrypoint: submits a transaction that invokes a precompile program directly or via a program that trusts the precompile result, placing the precompile instruction in a transaction that also carries a durable nonce advance
- Attacker controls: which precompile id is invoked, the precompile instruction data, and the surrounding instruction list
- Exploit idea: Reach execution with a precompile instruction that was never verified because it was invoked from an unexpected position.
- Invariant to test: Every precompile instruction in a transaction is verified before any instruction executes.
- Expected Immunefi impact: Critical - Loss of Funds (theft of funds without the owner's signature)
- Fast validation: unit-test verify_if_precompile with the crafted instruction and instruction_datas and assert an error is returned
