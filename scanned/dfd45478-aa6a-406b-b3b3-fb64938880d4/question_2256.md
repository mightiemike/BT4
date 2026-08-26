# Q2256: vm::deserialize_parameters - VM result mapped to success on an aborted program (exhausting the compute budget in the)

## Question
Can an unprivileged attacker who deploys and invokes its own SBF program so its bytecode is JIT-compiled and executed, exhausting the compute budget in the middle of a CPI, drive `vm::deserialize_parameters` to have execute report success for a program that trapped or exceeded its budget, so that the invariant that any VM trap or budget exhaustion fails the instruction is broken and the outcome is Loss of Funds (theft of funds without the owner's signature)?

## Target
- File/function: `program-runtime/src/vm.rs` -> `deserialize_parameters`
- Entrypoint: deploys and invokes its own SBF program so its bytecode is JIT-compiled and executed, exhausting the compute budget in the middle of a CPI
- Attacker controls: the ELF contents, entrypoint, heap request, stack usage and the instruction data passed in
- Exploit idea: Have execute report success for a program that trapped or exceeded its budget.
- Invariant to test: Any VM trap or budget exhaustion fails the instruction.
- Expected Immunefi impact: Critical - Loss of Funds (theft of funds without the owner's signature)
- Fast validation: program-runtime unit test executing the crafted program and asserting metering, heap cost and result handling are correct
