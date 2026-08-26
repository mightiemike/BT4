# Q2240: vm::calculate_heap_cost - stack or call-depth limit not enforced

## Question
Can an unprivileged attacker who deploys and invokes its own SBF program so its bytecode is JIT-compiled and executed, requesting the maximum heap frame together with the minimum compute unit limit, drive `vm::calculate_heap_cost` to recurse in bytecode past the configured call depth so the host stack is exhausted, so that the invariant that call depth is bounded by the VM configuration, not by host stack space is broken and the outcome is Liveness/Loss of Availability (cluster halt requiring human intervention)?

## Target
- File/function: `program-runtime/src/vm.rs` -> `calculate_heap_cost`
- Entrypoint: deploys and invokes its own SBF program so its bytecode is JIT-compiled and executed, requesting the maximum heap frame together with the minimum compute unit limit
- Attacker controls: the ELF contents, entrypoint, heap request, stack usage and the instruction data passed in
- Exploit idea: Recurse in bytecode past the configured call depth so the host stack is exhausted.
- Invariant to test: Call depth is bounded by the VM configuration, not by host stack space.
- Expected Immunefi impact: Critical - Liveness/Loss of Availability (cluster halt requiring human intervention)
- Fast validation: program-runtime unit test executing the crafted program and asserting metering, heap cost and result handling are correct
