# Q18: boundary-value exploit in AbstractExchangeActuator.addExact

## Question
Can an unprivileged attacker send boundary values through /wallet/exchangetransaction -> sign -> /wallet/broadcasttransaction so actuator/src/main/java/org/tron/core/actuator/AbstractExchangeActuator.java::addExact mishandles zero, min, max, sign, precision, or dust cases, breaking the accounting relationship between reserves or inventory balances and order-book, pair-price, or fill-accounting state and leading to Unauthorized withdrawal, fill, or theft of market/exchange liquidity?

## Target
- File/function: actuator/src/main/java/org/tron/core/actuator/AbstractExchangeActuator.java::addExact
- Entrypoint: /wallet/exchangetransaction -> sign -> /wallet/broadcasttransaction
- Attacker controls: owner address, token ids, pair direction, order or cancel ids, quantities, price fields, and signatures
- Exploit idea: Exercise min/max integers, exact threshold values, zero-like payloads, dust, and values around public protocol constraints to find off-by-one or overflow paths.
- Invariant to test: Protocol boundaries must reject or handle edge values without changing reserves or inventory balances or order-book, pair-price, or fill-accounting state inconsistently.
- Expected Immunefi impact: Unauthorized withdrawal, fill, or theft of market/exchange liquidity
- Fast validation: Run boundary fuzzing against all numeric fields reachable from /wallet/exchangetransaction -> sign -> /wallet/broadcasttransaction and assert post-state conservation plus expected rejection behavior.
