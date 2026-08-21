# Q3006: ExchangeCreateActuator: fee accounting bypass

## Question
Can an unprivileged attacker (broadcast transaction) abuse `ExchangeCreateActuator.calcFee` in `actuator/src/main/java/org/tron/core/actuator/ExchangeCreateActuator.java` — where the attacker structures ExchangeCreateActuator so ExchangeCreateActuator.calcFee returns less than the resource actually consumed by ExchangeCreateActuator.execute — to break the invariant that fee charged is >= real resource consumed for ExchangeCreateActuator, leading to: DoS via protocol implementation (Advanced)?

## Target
- File/function: `actuator/src/main/java/org/tron/core/actuator/ExchangeCreateActuator.java` -> `ExchangeCreateActuator.calcFee`
- Entrypoint: broadcast ExchangeCreateActuator shaped to minimize calcFee
- Attacker controls: request/transaction/contract inputs to `ExchangeCreateActuator.calcFee` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: structures ExchangeCreateActuator so ExchangeCreateActuator.calcFee returns less than the resource actually consumed by ExchangeCreateActuator.execute
- Invariant to test: fee charged is >= real resource consumed for ExchangeCreateActuator
- Expected Immunefi impact: DoS via protocol implementation (Advanced)
- Fast validation: compare calcFee to measured bandwidth/energy of execute
