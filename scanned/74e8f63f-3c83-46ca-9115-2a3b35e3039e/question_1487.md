# Q1487: receipt-trace mismatch in MarketAccountOrderCapsule.getOwnerAddress

## Question
Can an unprivileged attacker reach /wallet/exchangewithdraw -> sign -> /wallet/broadcasttransaction so chainbase/src/main/java/org/tron/core/capsule/MarketAccountOrderCapsule.java::getOwnerAddress records a receipt, trace, or historical artifact that disagrees with the durable reserves or inventory balances/order-book, pair-price, or fill-accounting state, enabling later logic to act on false settlement state and leading to Double fill, cancel, or exchange settlement?

## Target
- File/function: chainbase/src/main/java/org/tron/core/capsule/MarketAccountOrderCapsule.java::getOwnerAddress
- Entrypoint: /wallet/exchangewithdraw -> sign -> /wallet/broadcasttransaction
- Attacker controls: owner address, token ids, pair direction, order or cancel ids, quantities, price fields, and signatures
- Exploit idea: Focus on flows where receipts and traces are written separately from the balance or lifecycle state they describe.
- Invariant to test: Historical artifacts must faithfully describe the committed result and must not permit replay, double-claim, or false-spent decisions.
- Expected Immunefi impact: Double fill, cancel, or exchange settlement
- Fast validation: Force late failures and ambiguous outcomes via /wallet/exchangewithdraw -> sign -> /wallet/broadcasttransaction; compare durable state against every generated receipt, trace, and history record.
