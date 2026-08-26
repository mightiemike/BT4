# Q269: precompiles::new - instruction_datas slice does not match the executed instruction list (placing the precompile instruction in a)

## Question
Can an unprivileged attacker who submits a transaction that invokes a precompile program directly or via a program that trusts the precompile result, placing the precompile instruction in a transaction that also carries a durable nonce advance, drive `precompiles::new` to make the instruction_datas passed to verification differ from the instructions the runtime executes, so that the invariant that the data verified by the precompile is the data the transaction executes is broken and the outcome is Loss of Funds (theft of funds without the owner's signature)?

## Target
- File/function: `precompiles/src/lib.rs` -> `new`
- Entrypoint: submits a transaction that invokes a precompile program directly or via a program that trusts the precompile result, placing the precompile instruction in a transaction that also carries a durable nonce advance
- Attacker controls: which precompile id is invoked, the precompile instruction data, and the surrounding instruction list
- Exploit idea: Make the instruction_datas passed to verification differ from the instructions the runtime executes.
- Invariant to test: The data verified by the precompile is the data the transaction executes.
- Expected Immunefi impact: Critical - Loss of Funds (theft of funds without the owner's signature)
- Fast validation: unit-test verify_if_precompile with the crafted instruction and instruction_datas and assert an error is returned
