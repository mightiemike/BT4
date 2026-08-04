# Q505: revert-state leak in ChainParameterEnum.fromCode

## Question
Can an unprivileged attacker reach gRPC broadcastTransaction with crafted bytecode or calldata so actuator/src/main/java/org/tron/core/vm/ChainParameterEnum.java::fromCode mutates transaction-processing state before a revert or exceptional halt, fails to fully unwind the resulting accounting, receipt, or index state, and causes Deterministic invalid state divergence or unauthorized partial commit from a reverted execution?

## Target
- File/function: actuator/src/main/java/org/tron/core/vm/ChainParameterEnum.java::fromCode
- Entrypoint: gRPC broadcastTransaction
- Attacker controls: publicly supplied addresses, ids, amounts, signatures, indexes, and encoding flags
- Exploit idea: Force a late revert after partial writes, internal transfers, or refunds to check whether every side effect is unwound consistently.
- Invariant to test: A reverted TVM frame must leave transaction-processing state and the resulting accounting, receipt, or index state unchanged except for the intended fee burn.
- Expected Immunefi impact: Deterministic invalid state divergence or unauthorized partial commit from a reverted execution
- Fast validation: Deploy or call contracts that revert after internal writes through gRPC broadcastTransaction, then diff repository state, receipts, refunds, and logs against a clean baseline.
