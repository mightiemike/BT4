# Q2230: vm::execute - heap cost not charged for the requested frame

## Question
Can an unprivileged attacker who deploys and invokes its own SBF program so its bytecode is JIT-compiled and executed, requesting the maximum heap frame together with the minimum compute unit limit, drive `vm::execute` to obtain a heap frame whose calculate_heap_cost contribution is below the memory actually reserved, so that the invariant that heap cost in compute units is monotone in the bytes reserved is broken and the outcome is Liveness/Loss of Availability (block production degraded below usable capacity)?

## Target
- File/function: `program-runtime/src/vm.rs` -> `execute`
- Entrypoint: deploys and invokes its own SBF program so its bytecode is JIT-compiled and executed, requesting the maximum heap frame together with the minimum compute unit limit
- Attacker controls: the ELF contents, entrypoint, heap request, stack usage and the instruction data passed in
- Exploit idea: Obtain a heap frame whose calculate_heap_cost contribution is below the memory actually reserved.
- Invariant to test: Heap cost in compute units is monotone in the bytes reserved.
- Expected Immunefi impact: High - Liveness/Loss of Availability (block production degraded below usable capacity)
- Fast validation: program-runtime unit test executing the crafted program and asserting metering, heap cost and result handling are correct
