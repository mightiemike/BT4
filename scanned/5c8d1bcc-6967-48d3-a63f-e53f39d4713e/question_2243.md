# Q2243: vm::execute - VM result mapped to success on an aborted program (returning a non-zero program result after)

## Question
Can an unprivileged attacker who deploys and invokes its own SBF program so its bytecode is JIT-compiled and executed, returning a non-zero program result after modifying accounts, drive `vm::execute` to have execute report success for a program that trapped or exceeded its budget, so that the invariant that any VM trap or budget exhaustion fails the instruction is broken and the outcome is Loss of Funds (theft of funds without the owner's signature)?

## Target
- File/function: `program-runtime/src/vm.rs` -> `execute`
- Entrypoint: deploys and invokes its own SBF program so its bytecode is JIT-compiled and executed, returning a non-zero program result after modifying accounts
- Attacker controls: the ELF contents, entrypoint, heap request, stack usage and the instruction data passed in
- Exploit idea: Have execute report success for a program that trapped or exceeded its budget.
- Invariant to test: Any VM trap or budget exhaustion fails the instruction.
- Expected Immunefi impact: Critical - Loss of Funds (theft of funds without the owner's signature)
- Fast validation: program-runtime unit test executing the crafted program and asserting metering, heap cost and result handling are correct
