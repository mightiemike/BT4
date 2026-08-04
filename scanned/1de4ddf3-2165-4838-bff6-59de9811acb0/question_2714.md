# Q2714: energy-undercharge in VMConfig.initAllowHardenResourceCalculation

## Question
Can an unprivileged attacker use /jsonrpc eth_call / eth_estimateGas / eth_sendRawTransaction to make common/src/main/java/org/tron/core/vm/config/VMConfig.java::initAllowHardenResourceCalculation perform substantially more work than the Energy charged, or refund Energy that should remain burned, leading to Materially underpriced public execution work or deterministic node degradation on smart-contract input?

## Target
- File/function: common/src/main/java/org/tron/core/vm/config/VMConfig.java::initAllowHardenResourceCalculation
- Entrypoint: /jsonrpc eth_call / eth_estimateGas / eth_sendRawTransaction
- Attacker controls: contract bytecode, calldata, call value, fee limit, energy, nested-call structure, and storage keys
- Exploit idea: Target expansion, nested calls, precompiles, native opcodes, and exceptional exits where charge and refund accounting may diverge.
- Invariant to test: Charged Energy must conservatively upper-bound the real execution work and refunds must never exceed what was validly earned.
- Expected Immunefi impact: Materially underpriced public execution work or deterministic node degradation on smart-contract input
- Fast validation: Fuzz contracts that maximize work per charged unit via /jsonrpc eth_call / eth_estimateGas / eth_sendRawTransaction; compare measured execution effort against charged and refunded Energy.
