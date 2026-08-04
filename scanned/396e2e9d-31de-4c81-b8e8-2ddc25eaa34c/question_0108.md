# Q108: query-settlement mismatch in ExchangeCreateActuator.getOwnerAddress

## Question
Can an unprivileged attacker abuse /wallet/exchangecreate -> sign -> /wallet/broadcasttransaction so actuator/src/main/java/org/tron/core/actuator/ExchangeCreateActuator.java::getOwnerAddress computes the next state from a different source of truth than the later settlement path, letting publicly visible state and committed state diverge until Unauthorized withdrawal, fill, or theft of market/exchange liquidity occurs?

## Target
- File/function: actuator/src/main/java/org/tron/core/actuator/ExchangeCreateActuator.java::getOwnerAddress
- Entrypoint: /wallet/exchangecreate -> sign -> /wallet/broadcasttransaction
- Attacker controls: owner address, token ids, pair direction, order or cancel ids, quantities, price fields, and signatures
- Exploit idea: Compare preflight queries, transaction builders, and settlement code for mismatched stores, versioned ledgers, or stale snapshots that can be chained by a user.
- Invariant to test: The state shown to a user for a reachable exchange or market order flow must match the state the executor later uses when mutating reserves or inventory balances and order-book, pair-price, or fill-accounting state.
- Expected Immunefi impact: Unauthorized withdrawal, fill, or theft of market/exchange liquidity
- Fast validation: Chain the relevant read path and write path around /wallet/exchangecreate -> sign -> /wallet/broadcasttransaction; assert any quoted balance, allowance, reward, order, or note status matches the state actually consumed at settlement.
