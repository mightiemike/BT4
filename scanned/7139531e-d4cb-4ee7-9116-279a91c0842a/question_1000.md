# Q1000: ProposalApproveActuator: zero/self operand edge

## Question
Can an unprivileged attacker (broadcast transaction) abuse `ProposalApproveActuator.validate` in `actuator/src/main/java/org/tron/core/actuator/ProposalApproveActuator.java` — where the attacker submits ProposalApproveActuator with a zero amount, self-referential owner==to, or empty target that ProposalApproveActuator.validate fails to reject, corrupting downstream accounting — to break the invariant that ProposalApproveActuator.validate rejects zero, self, and empty operands that break accounting, leading to: Asset/accounting corruption (Critical)?

## Target
- File/function: `actuator/src/main/java/org/tron/core/actuator/ProposalApproveActuator.java` -> `ProposalApproveActuator.validate`
- Entrypoint: broadcast ProposalApproveActuator with zero/self operand
- Attacker controls: request/transaction/contract inputs to `ProposalApproveActuator.validate` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: submits ProposalApproveActuator with a zero amount, self-referential owner==to, or empty target that ProposalApproveActuator.validate fails to reject, corrupting downstream accounting
- Invariant to test: ProposalApproveActuator.validate rejects zero, self, and empty operands that break accounting
- Expected Immunefi impact: Asset/accounting corruption (Critical)
- Fast validation: JUnit with amount=0 and owner==to asserting rejection
