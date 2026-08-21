# Q2274: AbstractExchangeActuator: validate/execute state drift

## Question
Can an unprivileged attacker (broadcast transaction) abuse `AbstractExchangeActuator.subtractExact` in `actuator/src/main/java/org/tron/core/actuator/AbstractExchangeActuator.java` — where the attacker crafts a contract where AbstractExchangeActuator.validate passes but state read in AbstractExchangeActuator.execute has changed, letting execute mutate on a stale precondition — to break the invariant that every precondition checked in validate still holds at execute against committed state, leading to: Asset/accounting corruption (Critical)?

## Target
- File/function: `actuator/src/main/java/org/tron/core/actuator/AbstractExchangeActuator.java` -> `AbstractExchangeActuator.subtractExact`
- Entrypoint: broadcast a contract routed to AbstractExchangeActuator
- Attacker controls: request/transaction/contract inputs to `AbstractExchangeActuator.subtractExact` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: crafts a contract where AbstractExchangeActuator.validate passes but state read in AbstractExchangeActuator.execute has changed, letting execute mutate on a stale precondition
- Invariant to test: every precondition checked in validate still holds at execute against committed state
- Expected Immunefi impact: Asset/accounting corruption (Critical)
- Fast validation: order two txs so validate precondition is invalidated before execute
