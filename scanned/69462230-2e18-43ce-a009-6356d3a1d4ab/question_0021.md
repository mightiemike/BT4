# Q21: ResourceProcessor: exchange reserve manipulation

## Question
Can an unprivileged attacker (broadcast transaction) abuse `ResourceProcessor.unDelegateIncreaseV2` in `chainbase/src/main/java/org/tron/core/db/ResourceProcessor.java` — where the attacker uses ResourceProcessor.unDelegateIncreaseV2 to inject/withdraw/trade so exchange reserves drift from the constant-product/expected invariant — to break the invariant that exchange reserve math in ResourceProcessor.unDelegateIncreaseV2 preserves its stated invariant, leading to: Asset/accounting corruption (Critical)?

## Target
- File/function: `chainbase/src/main/java/org/tron/core/db/ResourceProcessor.java` -> `ResourceProcessor.unDelegateIncreaseV2`
- Entrypoint: broadcast exchange ops via ResourceProcessor.unDelegateIncreaseV2
- Attacker controls: request/transaction/contract inputs to `ResourceProcessor.unDelegateIncreaseV2` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: uses ResourceProcessor.unDelegateIncreaseV2 to inject/withdraw/trade so exchange reserves drift from the constant-product/expected invariant
- Invariant to test: exchange reserve math in ResourceProcessor.unDelegateIncreaseV2 preserves its stated invariant
- Expected Immunefi impact: Asset/accounting corruption (Critical)
- Fast validation: JUnit trading across boundaries asserting reserve invariant
