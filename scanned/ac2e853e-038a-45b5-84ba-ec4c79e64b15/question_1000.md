# Q1000: precompile-canonicalization mismatch in ProgramInvokeImpl.getCallerAddress

## Question
Can an unprivileged attacker pass edge-case inputs through /wallet/triggerconstantcontract so actuator/src/main/java/org/tron/core/vm/program/invoke/ProgramInvokeImpl.java::getCallerAddress feeds a precompile or native contract with non-canonical data, causing different outputs, authorization results, or charges than another honest node would compute and leading to Deterministic invalid state divergence on public smart-contract input?

## Target
- File/function: actuator/src/main/java/org/tron/core/vm/program/invoke/ProgramInvokeImpl.java::getCallerAddress
- Entrypoint: /wallet/triggerconstantcontract
- Attacker controls: contract bytecode, calldata, call value, fee limit, energy, nested-call structure, and storage keys
- Exploit idea: Probe length prefixes, empty values, non-canonical encodings, duplicate fields, and boundary inputs around native or precompiled operations.
- Invariant to test: For the same public input, every honest node must derive the same precompile inputs, outputs, charges, and side effects.
- Expected Immunefi impact: Deterministic invalid state divergence on public smart-contract input
- Fast validation: Differential-test the same contract call via /wallet/triggerconstantcontract across repeated executions and alternate encodings; assert identical result bytes, charges, and receipts.
