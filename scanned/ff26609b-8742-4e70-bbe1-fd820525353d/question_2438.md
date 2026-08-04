# Q2438: energy-undercharge in StorageRowStore.get

## Question
Can an unprivileged attacker use /wallet/triggerconstantcontract to make chainbase/src/main/java/org/tron/core/store/StorageRowStore.java::get perform substantially more work than the Energy charged, or refund Energy that should remain burned, leading to Materially underpriced public execution work or deterministic node degradation on smart-contract input?

## Target
- File/function: chainbase/src/main/java/org/tron/core/store/StorageRowStore.java::get
- Entrypoint: /wallet/triggerconstantcontract
- Attacker controls: contract bytecode, calldata, call value, fee limit, energy, nested-call structure, and storage keys
- Exploit idea: Target expansion, nested calls, precompiles, native opcodes, and exceptional exits where charge and refund accounting may diverge.
- Invariant to test: Charged Energy must conservatively upper-bound the real execution work and refunds must never exceed what was validly earned.
- Expected Immunefi impact: Materially underpriced public execution work or deterministic node degradation on smart-contract input
- Fast validation: Fuzz contracts that maximize work per charged unit via /wallet/triggerconstantcontract; compare measured execution effort against charged and refunded Energy.
