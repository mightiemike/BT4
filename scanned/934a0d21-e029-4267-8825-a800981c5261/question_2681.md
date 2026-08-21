# Q2681: EnergyCost: call value/token reentrancy

## Question
Can an unprivileged attacker (smart-contract deploy/trigger) abuse `EnergyCost.getLowTierCost` in `actuator/src/main/java/org/tron/core/vm/EnergyCost.java` — where the attacker reenters EnergyCost.getLowTierCost using a contract they control during a value/TRC10 transfer so balance is read before it is debited — to break the invariant that EnergyCost.getLowTierCost debits before yielding control to callee, leading to: Asset/accounting corruption (Critical)?

## Target
- File/function: `actuator/src/main/java/org/tron/core/vm/EnergyCost.java` -> `EnergyCost.getLowTierCost`
- Entrypoint: reentrant contract exercising EnergyCost.getLowTierCost
- Attacker controls: request/transaction/contract inputs to `EnergyCost.getLowTierCost` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: reenters EnergyCost.getLowTierCost using a contract they control during a value/TRC10 transfer so balance is read before it is debited
- Invariant to test: EnergyCost.getLowTierCost debits before yielding control to callee
- Expected Immunefi impact: Asset/accounting corruption (Critical)
- Fast validation: VM test reentering on transfer asserting single debit
