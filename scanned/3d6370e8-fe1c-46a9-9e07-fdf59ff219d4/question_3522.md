# Q3522: UnfreezeBalanceV2Actuator: balance underflow

## Question
Can an unprivileged attacker (broadcast transaction) abuse `UnfreezeBalanceV2Actuator.validate` in `actuator/src/main/java/org/tron/core/actuator/UnfreezeBalanceV2Actuator.java` — where the attacker sizes amounts in UnfreezeBalanceV2Actuator so a subtraction in UnfreezeBalanceV2Actuator.execute underflows or a sum overflows past the conservation check — to break the invariant that balances and supply never underflow or exceed issuance in UnfreezeBalanceV2Actuator, leading to: Asset/accounting corruption (Critical)?

## Target
- File/function: `actuator/src/main/java/org/tron/core/actuator/UnfreezeBalanceV2Actuator.java` -> `UnfreezeBalanceV2Actuator.validate`
- Entrypoint: broadcast UnfreezeBalanceV2Actuator with boundary amounts
- Attacker controls: request/transaction/contract inputs to `UnfreezeBalanceV2Actuator.validate` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: sizes amounts in UnfreezeBalanceV2Actuator so a subtraction in UnfreezeBalanceV2Actuator.execute underflows or a sum overflows past the conservation check
- Invariant to test: balances and supply never underflow or exceed issuance in UnfreezeBalanceV2Actuator
- Expected Immunefi impact: Asset/accounting corruption (Critical)
- Fast validation: JUnit execute() with MAX/near-zero balances asserting conservation
