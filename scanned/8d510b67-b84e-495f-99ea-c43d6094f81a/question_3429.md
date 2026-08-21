# Q3429: FreezeBalanceV2Actuator: zero/self operand edge

## Question
Can an unprivileged attacker (broadcast transaction) abuse `FreezeBalanceV2Actuator.calcFee` in `actuator/src/main/java/org/tron/core/actuator/FreezeBalanceV2Actuator.java` — where the attacker submits FreezeBalanceV2Actuator with a zero amount, self-referential owner==to, or empty target that FreezeBalanceV2Actuator.validate fails to reject, corrupting downstream accounting — to break the invariant that FreezeBalanceV2Actuator.validate rejects zero, self, and empty operands that break accounting, leading to: Asset/accounting corruption (Critical)?

## Target
- File/function: `actuator/src/main/java/org/tron/core/actuator/FreezeBalanceV2Actuator.java` -> `FreezeBalanceV2Actuator.calcFee`
- Entrypoint: broadcast FreezeBalanceV2Actuator with zero/self operand
- Attacker controls: request/transaction/contract inputs to `FreezeBalanceV2Actuator.calcFee` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: submits FreezeBalanceV2Actuator with a zero amount, self-referential owner==to, or empty target that FreezeBalanceV2Actuator.validate fails to reject, corrupting downstream accounting
- Invariant to test: FreezeBalanceV2Actuator.validate rejects zero, self, and empty operands that break accounting
- Expected Immunefi impact: Asset/accounting corruption (Critical)
- Fast validation: JUnit with amount=0 and owner==to asserting rejection
