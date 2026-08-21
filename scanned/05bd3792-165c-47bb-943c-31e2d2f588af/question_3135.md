# Q3135: MarketSellAssetActuator: duplicate/replay effect

## Question
Can an unprivileged attacker (broadcast transaction) abuse `MarketSellAssetActuator.execute` in `actuator/src/main/java/org/tron/core/actuator/MarketSellAssetActuator.java` — where the attacker replays or batches MarketSellAssetActuator exploiting a missing idempotency or index-key check to repeat its effect — to break the invariant that MarketSellAssetActuator applies its effect exactly once per authorized intent, leading to: Asset/accounting corruption (Critical)?

## Target
- File/function: `actuator/src/main/java/org/tron/core/actuator/MarketSellAssetActuator.java` -> `MarketSellAssetActuator.execute`
- Entrypoint: broadcast MarketSellAssetActuator twice with same/varied ids
- Attacker controls: request/transaction/contract inputs to `MarketSellAssetActuator.execute` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: replays or batches MarketSellAssetActuator exploiting a missing idempotency or index-key check to repeat its effect
- Invariant to test: MarketSellAssetActuator applies its effect exactly once per authorized intent
- Expected Immunefi impact: Asset/accounting corruption (Critical)
- Fast validation: JUnit executing MarketSellAssetActuator twice and asserting single effect
