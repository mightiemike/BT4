# Q463: ResourceProcessor: exchange reserve manipulation

## Question
Can an unprivileged attacker (broadcast transaction) abuse `ResourceProcessor.hardenCalculation` in `chainbase/src/main/java/org/tron/core/db/ResourceProcessor.java` — where the attacker uses ResourceProcessor.hardenCalculation to inject/withdraw/trade so exchange reserves drift from the constant-product/expected invariant — to break the invariant that exchange reserve math in ResourceProcessor.hardenCalculation preserves its stated invariant, leading to: Asset/accounting corruption (Critical)?

## Target
- File/function: `chainbase/src/main/java/org/tron/core/db/ResourceProcessor.java` -> `ResourceProcessor.hardenCalculation`
- Entrypoint: broadcast exchange ops via ResourceProcessor.hardenCalculation
- Attacker controls: request/transaction/contract inputs to `ResourceProcessor.hardenCalculation` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: uses ResourceProcessor.hardenCalculation to inject/withdraw/trade so exchange reserves drift from the constant-product/expected invariant
- Invariant to test: exchange reserve math in ResourceProcessor.hardenCalculation preserves its stated invariant
- Expected Immunefi impact: Asset/accounting corruption (Critical)
- Fast validation: JUnit trading across boundaries asserting reserve invariant
