# Q125: failure rollback leak in ExchangeTransactionActuator.execute

## Question
Can an unprivileged attacker use /wallet/exchangetransaction -> sign -> /wallet/broadcasttransaction to trigger a late failure after partial mutation in actuator/src/main/java/org/tron/core/actuator/ExchangeTransactionActuator.java::execute, leaving reserves or inventory balances changed while order-book, pair-price, or fill-accounting state is rolled back or vice versa, and thereby causing Unauthorized withdrawal, fill, or theft of market/exchange liquidity?

## Target
- File/function: actuator/src/main/java/org/tron/core/actuator/ExchangeTransactionActuator.java::execute
- Entrypoint: /wallet/exchangetransaction -> sign -> /wallet/broadcasttransaction
- Attacker controls: owner address, token ids, pair direction, order or cancel ids, quantities, price fields, and signatures
- Exploit idea: Force failures after the first ledger write, secondary index update, or reward/fee adjustment to see whether cleanup is asymmetric.
- Invariant to test: A failed exchange or market order flow must not leave surviving partial effects in reserves or inventory balances or order-book, pair-price, or fill-accounting state, except for the intended fee burn.
- Expected Immunefi impact: Unauthorized withdrawal, fill, or theft of market/exchange liquidity
- Fast validation: Inject values that fail after partial progress through /wallet/exchangetransaction -> sign -> /wallet/broadcasttransaction, then compare all touched ledgers and indexes against a clean pre-state snapshot.
