# Q1579: cross-store atomicity bug in SafeExchangeProcessor.exchangeToSupply

## Question
Can an unprivileged attacker use /wallet/exchangewithdraw -> sign -> /wallet/broadcasttransaction so chainbase/src/main/java/org/tron/core/capsule/SafeExchangeProcessor.java::exchangeToSupply updates one store, index, or capsule successfully and another fails, leaving the system in a mixed atomicity state that leads to Unauthorized withdrawal, fill, or theft of market/exchange liquidity?

## Target
- File/function: chainbase/src/main/java/org/tron/core/capsule/SafeExchangeProcessor.java::exchangeToSupply
- Entrypoint: /wallet/exchangewithdraw -> sign -> /wallet/broadcasttransaction
- Attacker controls: owner address, token ids, pair direction, order or cancel ids, quantities, price fields, and signatures
- Exploit idea: Look for flows where balances, indexes, receipts, reward state, and note state are written in separate steps without one all-or-nothing guard.
- Invariant to test: A public action that spans multiple stores must either commit all required writes or none of them.
- Expected Immunefi impact: Unauthorized withdrawal, fill, or theft of market/exchange liquidity
- Fast validation: Fault-inject failures after each individual write reachable from /wallet/exchangewithdraw -> sign -> /wallet/broadcasttransaction; assert no single-store commit can survive alone.
