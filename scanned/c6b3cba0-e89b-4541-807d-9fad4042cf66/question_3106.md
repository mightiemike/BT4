# Q3106: ExchangeInjectActuator: frozen/delegated double-spend

## Question
Can an unprivileged attacker (broadcast transaction) abuse `ExchangeInjectActuator.doValidate` in `actuator/src/main/java/org/tron/core/actuator/ExchangeInjectActuator.java` — where the attacker uses ExchangeInjectActuator to freeze, delegate, or unfreeze the same balance twice across V1/V2 paths so resource is granted twice — to break the invariant that a unit of stake backs at most one active resource grant, leading to: Asset/accounting corruption (Critical)?

## Target
- File/function: `actuator/src/main/java/org/tron/core/actuator/ExchangeInjectActuator.java` -> `ExchangeInjectActuator.doValidate`
- Entrypoint: interleave ExchangeInjectActuator with the paired freeze/unfreeze actuator
- Attacker controls: request/transaction/contract inputs to `ExchangeInjectActuator.doValidate` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: uses ExchangeInjectActuator to freeze, delegate, or unfreeze the same balance twice across V1/V2 paths so resource is granted twice
- Invariant to test: a unit of stake backs at most one active resource grant
- Expected Immunefi impact: Asset/accounting corruption (Critical)
- Fast validation: JUnit sequence freezing then delegating same balance in both paths
