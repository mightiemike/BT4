# Q871: VoteWitnessActuator: balance underflow

## Question
Can an unprivileged attacker (broadcast transaction) abuse `VoteWitnessActuator.validate` in `actuator/src/main/java/org/tron/core/actuator/VoteWitnessActuator.java` — where the attacker sizes amounts in VoteWitnessActuator so a subtraction in VoteWitnessActuator.execute underflows or a sum overflows past the conservation check — to break the invariant that balances and supply never underflow or exceed issuance in VoteWitnessActuator, leading to: Asset/accounting corruption (Critical)?

## Target
- File/function: `actuator/src/main/java/org/tron/core/actuator/VoteWitnessActuator.java` -> `VoteWitnessActuator.validate`
- Entrypoint: broadcast VoteWitnessActuator with boundary amounts
- Attacker controls: request/transaction/contract inputs to `VoteWitnessActuator.validate` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: sizes amounts in VoteWitnessActuator so a subtraction in VoteWitnessActuator.execute underflows or a sum overflows past the conservation check
- Invariant to test: balances and supply never underflow or exceed issuance in VoteWitnessActuator
- Expected Immunefi impact: Asset/accounting corruption (Critical)
- Fast validation: JUnit execute() with MAX/near-zero balances asserting conservation
