# Q541: revert-state leak in LogInfoTriggerParser.getEntrySignature

## Question
Can an unprivileged attacker reach /wallet/estimateenergy with crafted bytecode or calldata so actuator/src/main/java/org/tron/core/vm/LogInfoTriggerParser.java::getEntrySignature mutates TVM storage, balances, or repository state before a revert or exceptional halt, fails to fully unwind receipts, refunds, internal transfers, or log state, and causes Deterministic invalid state divergence or unauthorized partial commit from a reverted execution?

## Target
- File/function: actuator/src/main/java/org/tron/core/vm/LogInfoTriggerParser.java::getEntrySignature
- Entrypoint: /wallet/estimateenergy
- Attacker controls: contract bytecode, calldata, call value, fee limit, energy, nested-call structure, and storage keys
- Exploit idea: Force a late revert after partial writes, internal transfers, or refunds to check whether every side effect is unwound consistently.
- Invariant to test: A reverted TVM frame must leave TVM storage, balances, or repository state and receipts, refunds, internal transfers, or log state unchanged except for the intended fee burn.
- Expected Immunefi impact: Deterministic invalid state divergence or unauthorized partial commit from a reverted execution
- Fast validation: Deploy or call contracts that revert after internal writes through /wallet/estimateenergy, then diff repository state, receipts, refunds, and logs against a clean baseline.
