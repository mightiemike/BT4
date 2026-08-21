# Q2608: UpdateEnergyLimitContractActuator: expiration/time boundary

## Question
Can an unprivileged attacker (broadcast transaction) abuse `UpdateEnergyLimitContractActuator.execute` in `actuator/src/main/java/org/tron/core/actuator/UpdateEnergyLimitContractActuator.java` — where the attacker exploits an off-by-one in UpdateEnergyLimitContractActuator's time/expire/maintenance comparison to unfreeze or withdraw early — to break the invariant that time-gated state in UpdateEnergyLimitContractActuator changes only at or after the true boundary, leading to: Asset/accounting corruption (Critical)?

## Target
- File/function: `actuator/src/main/java/org/tron/core/actuator/UpdateEnergyLimitContractActuator.java` -> `UpdateEnergyLimitContractActuator.execute`
- Entrypoint: broadcast UpdateEnergyLimitContractActuator at the expire boundary
- Attacker controls: request/transaction/contract inputs to `UpdateEnergyLimitContractActuator.execute` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: exploits an off-by-one in UpdateEnergyLimitContractActuator's time/expire/maintenance comparison to unfreeze or withdraw early
- Invariant to test: time-gated state in UpdateEnergyLimitContractActuator changes only at or after the true boundary
- Expected Immunefi impact: Asset/accounting corruption (Critical)
- Fast validation: JUnit at expireTime-1/expireTime asserting correct gating
