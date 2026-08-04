# Q401: failure rollback leak in VMActuator.execute

## Question
Can an unprivileged attacker use /wallet/estimateenergy to trigger a late failure after partial mutation in actuator/src/main/java/org/tron/core/actuator/VMActuator.java::execute, leaving TVM storage, balances, or repository state changed while receipts, refunds, internal transfers, or log state is rolled back or vice versa, and thereby causing Unauthorized internal value movement or state mutation?

## Target
- File/function: actuator/src/main/java/org/tron/core/actuator/VMActuator.java::execute
- Entrypoint: /wallet/estimateenergy
- Attacker controls: contract bytecode, calldata, call value, fee limit, energy, nested-call structure, and storage keys
- Exploit idea: Force failures after the first ledger write, secondary index update, or reward/fee adjustment to see whether cleanup is asymmetric.
- Invariant to test: A failed contract deploy, call, estimate, or execution flow must not leave surviving partial effects in TVM storage, balances, or repository state or receipts, refunds, internal transfers, or log state, except for the intended fee burn.
- Expected Immunefi impact: Unauthorized internal value movement or state mutation
- Fast validation: Inject values that fail after partial progress through /wallet/estimateenergy, then compare all touched ledgers and indexes against a clean pre-state snapshot.
