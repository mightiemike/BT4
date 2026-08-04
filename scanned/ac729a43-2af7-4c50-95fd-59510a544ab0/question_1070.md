# Q1070: energy-undercharge in MUtil.checkCPUTimeForCreate2

## Question
Can an unprivileged attacker use gRPC broadcastTransaction to make actuator/src/main/java/org/tron/core/vm/utils/MUtil.java::checkCPUTimeForCreate2 perform substantially more work than the Energy charged, or refund Energy that should remain burned, leading to Materially underpriced public execution work or deterministic node degradation on smart-contract input?

## Target
- File/function: actuator/src/main/java/org/tron/core/vm/utils/MUtil.java::checkCPUTimeForCreate2
- Entrypoint: gRPC broadcastTransaction
- Attacker controls: publicly supplied addresses, ids, amounts, signatures, indexes, and encoding flags
- Exploit idea: Target expansion, nested calls, precompiles, native opcodes, and exceptional exits where charge and refund accounting may diverge.
- Invariant to test: Charged Energy must conservatively upper-bound the real execution work and refunds must never exceed what was validly earned.
- Expected Immunefi impact: Materially underpriced public execution work or deterministic node degradation on smart-contract input
- Fast validation: Fuzz contracts that maximize work per charged unit via gRPC broadcastTransaction; compare measured execution effort against charged and refunded Energy.
