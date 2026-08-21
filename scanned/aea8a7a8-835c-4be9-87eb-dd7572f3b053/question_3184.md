# Q3184: MarketCancelOrderActuator: expiration/time boundary

## Question
Can an unprivileged attacker (broadcast transaction) abuse `MarketCancelOrderActuator.calcFee` in `actuator/src/main/java/org/tron/core/actuator/MarketCancelOrderActuator.java` — where the attacker exploits an off-by-one in MarketCancelOrderActuator's time/expire/maintenance comparison to unfreeze or withdraw early — to break the invariant that time-gated state in MarketCancelOrderActuator changes only at or after the true boundary, leading to: Asset/accounting corruption (Critical)?

## Target
- File/function: `actuator/src/main/java/org/tron/core/actuator/MarketCancelOrderActuator.java` -> `MarketCancelOrderActuator.calcFee`
- Entrypoint: broadcast MarketCancelOrderActuator at the expire boundary
- Attacker controls: request/transaction/contract inputs to `MarketCancelOrderActuator.calcFee` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: exploits an off-by-one in MarketCancelOrderActuator's time/expire/maintenance comparison to unfreeze or withdraw early
- Invariant to test: time-gated state in MarketCancelOrderActuator changes only at or after the true boundary
- Expected Immunefi impact: Asset/accounting corruption (Critical)
- Fast validation: JUnit at expireTime-1/expireTime asserting correct gating
