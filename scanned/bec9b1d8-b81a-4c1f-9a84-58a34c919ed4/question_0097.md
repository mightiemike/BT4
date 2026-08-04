# Q97: owner-binding bypass in ExchangeCreateActuator.validate

## Question
Can an unprivileged attacker enter through /wallet/exchangecreate -> sign -> /wallet/broadcasttransaction with crafted ownership fields and permission metadata so actuator/src/main/java/org/tron/core/actuator/ExchangeCreateActuator.java::validate binds authorization to the wrong account, mutates reserves or inventory balances and order-book, pair-price, or fill-accounting state on behalf of a victim, and leads to Unauthorized withdrawal, fill, or theft of market/exchange liquidity?

## Target
- File/function: actuator/src/main/java/org/tron/core/actuator/ExchangeCreateActuator.java::validate
- Entrypoint: /wallet/exchangecreate -> sign -> /wallet/broadcasttransaction
- Attacker controls: owner address, token ids, pair direction, order or cancel ids, quantities, price fields, and signatures
- Exploit idea: Try to make ownership resolution, permission selection, or caller binding point at a victim while the rest of the payload stays attacker-controlled.
- Invariant to test: Only the signer set that satisfies the required permission should be able to change reserves or inventory balances or order-book, pair-price, or fill-accounting state.
- Expected Immunefi impact: Unauthorized withdrawal, fill, or theft of market/exchange liquidity
- Fast validation: Create attacker and victim accounts, fuzz ownership and permission fields through /wallet/exchangecreate -> sign -> /wallet/broadcasttransaction, and assert victim-side reserves or inventory balances/order-book, pair-price, or fill-accounting state never change without victim signatures.
