# Q1903: VoteWitnessActuator: zero/self operand edge

## Question
Can an unprivileged attacker (broadcast transaction) abuse `VoteWitnessActuator.execute` in `actuator/src/main/java/org/tron/core/actuator/VoteWitnessActuator.java` — where the attacker submits VoteWitnessActuator with a zero amount, self-referential owner==to, or empty target that VoteWitnessActuator.validate fails to reject, corrupting downstream accounting — to break the invariant that VoteWitnessActuator.validate rejects zero, self, and empty operands that break accounting, leading to: Asset/accounting corruption (Critical)?

## Target
- File/function: `actuator/src/main/java/org/tron/core/actuator/VoteWitnessActuator.java` -> `VoteWitnessActuator.execute`
- Entrypoint: broadcast VoteWitnessActuator with zero/self operand
- Attacker controls: request/transaction/contract inputs to `VoteWitnessActuator.execute` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: submits VoteWitnessActuator with a zero amount, self-referential owner==to, or empty target that VoteWitnessActuator.validate fails to reject, corrupting downstream accounting
- Invariant to test: VoteWitnessActuator.validate rejects zero, self, and empty operands that break accounting
- Expected Immunefi impact: Asset/accounting corruption (Critical)
- Fast validation: JUnit with amount=0 and owner==to asserting rejection
