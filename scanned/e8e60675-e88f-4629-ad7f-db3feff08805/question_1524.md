# Q1524: versioned-store inconsistency in MarketPriceCapsule.getSellTokenQuantity

## Question
Can an unprivileged attacker drive /wallet/exchangewithdraw -> sign -> /wallet/broadcasttransaction through a v1/v2 or legacy/current compatibility path so chainbase/src/main/java/org/tron/core/capsule/MarketPriceCapsule.java::getSellTokenQuantity mutates reserves or inventory balances in one versioned store but resolves order-book, pair-price, or fill-accounting state from another, leading to Unauthorized withdrawal, fill, or theft of market/exchange liquidity?

## Target
- File/function: chainbase/src/main/java/org/tron/core/capsule/MarketPriceCapsule.java::getSellTokenQuantity
- Entrypoint: /wallet/exchangewithdraw -> sign -> /wallet/broadcasttransaction
- Attacker controls: owner address, token ids, pair direction, order or cancel ids, quantities, price fields, and signatures
- Exploit idea: Compare legacy/current stake, asset, exchange, delegation, and account state paths that are still reachable from public APIs.
- Invariant to test: Versioned compatibility layers must keep one coherent state view and must not let one public action read one version while writing another.
- Expected Immunefi impact: Unauthorized withdrawal, fill, or theft of market/exchange liquidity
- Fast validation: Run the same logical action across every legacy/current route via /wallet/exchangewithdraw -> sign -> /wallet/broadcasttransaction; assert all versioned stores observe identical balances and lifecycle state.
