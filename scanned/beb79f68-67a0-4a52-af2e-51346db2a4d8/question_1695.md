# Q1695: snapshot-rollback drift in MarketUtils.createPairPriceKey

## Question
Can an unprivileged attacker trigger /wallet/marketcancelorder -> sign -> /wallet/broadcasttransaction so chainbase/src/main/java/org/tron/core/capsule/utils/MarketUtils.java::createPairPriceKey rolls back one store view but leaves another advanced, separating reserves or inventory balances from order-book, pair-price, or fill-accounting state and leading to Deterministic invalid state divergence or unauthorized partial settlement?

## Target
- File/function: chainbase/src/main/java/org/tron/core/capsule/utils/MarketUtils.java::createPairPriceKey
- Entrypoint: /wallet/marketcancelorder -> sign -> /wallet/broadcasttransaction
- Attacker controls: owner address, token ids, pair direction, order or cancel ids, quantities, price fields, and signatures
- Exploit idea: Focus on nested snapshots, revoking stores, and multi-store flows that cross account, order, note, reward, or receipt state.
- Invariant to test: Rollback must restore one coherent state across all touched stores and indexes for a failed public action.
- Expected Immunefi impact: Deterministic invalid state divergence or unauthorized partial settlement
- Fast validation: Force failures after each write point via /wallet/marketcancelorder -> sign -> /wallet/broadcasttransaction, then compare all affected stores to a pristine snapshot.
